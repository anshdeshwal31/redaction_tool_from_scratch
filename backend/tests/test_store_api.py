"""A1 annotation storage and local API on synthetic documents (plan §4.10, §9.2-§9.4)."""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from redactor.core.canonical import write_canonical
from redactor.dataset import store
from redactor.dataset.models import AnnotationSet, Entity, Provenance, Region, SilverInfo, TextAnchor
from redactor.dataset.register import register
from redactor.extraction import cache
from redactor.extraction.pipeline import extract_document
from redactor.ingest.metadata import classify_all
from redactor.synth import pdfs

LINES = ["Synthetic claimant Ms Wren Ashdale attended on 3 March 2024.", "Contact Tamsin Hollow on 0491 570 156."]


@pytest.fixture()
def repo(data_root):
    src = data_root / "golden_dataset_docs"
    src.mkdir()
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        pdfs.born_digital(src / name, [LINES + [name]])
    register(src, enforce_expected=False)
    classify_all()
    for d in ("doc_001", "doc_002", "doc_003"):
        extract_document(d)
    return data_root


def entity(doc, surface, etype="PERSON", *, eid=1, role="plaintiff", origin="manual", action="KEEP", cid="matter_001/person_001"):
    pe = cache.load_reference_pages(doc)[1]
    s = pe.text.index(surface)
    e = s + len(surface)
    from redactor.core.types import BBox
    box = BBox.union_all(w.bbox for w in pe.words_in_span(s, e))
    return Entity(entity_id=f"{doc}.e{eid:04d}", canonical_id=cid if etype == "PERSON" else None, entity_type=etype, text=surface,
                  page=1, regions=[Region(x0=box.x0, y0=box.y0, x1=box.x1, y1=box.y1)],
                  text_anchor=TextAnchor(text_source_id=pe.text_source_id, start=s, end=e), role=role if etype == "PERSON" else None,
                  action=action, provenance=Provenance(annotator="A1", pass_=1, origin=origin))


def registry_with_person(annotator):
    from redactor.dataset.models import CanonicalRegistry, RegistryEntity
    reg = CanonicalRegistry(matter_id="matter_001", annotator=annotator, entities=[
        RegistryEntity(canonical_id="matter_001/person_001", entity_type="PERSON", role="plaintiff", gender="unknown", label="x")])
    write_canonical(store.registry_path(annotator), reg.dump())


def test_visibility_and_candidates(repo):
    assert store.split_of("doc_001") == "test"
    assert store.candidates_allowed("doc_002") and not store.candidates_allowed("doc_001") and not store.candidates_allowed("doc_003")
    assert store.skeleton("A1", "doc_002").mode == "candidates_shown"
    assert store.skeleton("A1", "doc_003").mode == "blind" and store.skeleton("A1", "doc_001").mode == "blind"
    assert not store.can_view("A1", "A2", "doc_003") and not store.can_view("A2", "A1", "doc_003")
    assert store.can_view("A1", "claude_silver", "doc_002") and not store.can_view("A1", "claude_silver", "doc_003")
    assert not store.can_view("A2", "claude_silver", "doc_002")
    assert store.can_view("adjudicator", "A2", "doc_003")
    assert not store.can_write("A1", "claude_silver") and not store.can_write("A1", "A2") and not store.can_write("A1", "adjudicated")


def test_save_revision_policy_validation_and_submit(repo):
    registry_with_person("A1")
    ann = store.skeleton("A1", "doc_002").model_copy(update={"entities": [entity("doc_002", "Wren Ashdale", action="KEEP")]})
    out = store.save("A1", "A1", "doc_002", ann, 0)
    assert out["revision"] == 1
    saved = store.load("A1", "doc_002")
    assert saved.entities[0].action == "SYNTHETIC"          # derived from the policy, not from the client
    with pytest.raises(store.StoreError) as e:
        store.save("A1", "A1", "doc_002", ann, 0)
    assert e.value.code == "revision_conflict" and e.value.status == 409
    bad = ann.model_copy(update={"entities": [entity("doc_002", "Wren Ashdale"), entity("doc_002", "Ashdale", eid=2)]})
    with pytest.raises(store.StoreError) as e:
        store.save("A1", "A1", "doc_002", bad, 1)
    assert e.value.code == "validation_failed" and "overlapping_spans" in e.value.detail
    with pytest.raises(store.StoreError) as e:
        store.save("A1", "claude_silver", "doc_002", ann, 0)
    assert e.value.status == 403
    store.submit("A1", "A1", "doc_002")
    with pytest.raises(store.StoreError) as e:
        store.save("A1", "A1", "doc_002", ann, 1)
    assert e.value.code == "submitted_files_are_final"


def _silver(doc):
    sk = store.skeleton("A1", doc)
    ents = [entity(doc, "Wren Ashdale", origin="silver").model_copy(update={"provenance": Provenance(annotator="claude_silver", pass_=1, origin="silver")})]
    ann = AnnotationSet(document_id=doc, document_sha256=sk.document_sha256, annotator="claude_silver", mode="silver",
                        guidelines_version="0.1.0", policy_ref=sk.policy_ref, coverage=sk.coverage.model_copy(update={"pages_complete": [1]}),
                        entities=ents, silver=SilverInfo(model_id="m", run_date="2026-01-01", guidelines_version="0.1.0",
                                                         prompt_sha256="0" * 64, input_sha256s={}))
    write_canonical(store.annotation_path("claude_silver", doc), store.apply_policy(ann).dump())


def test_silver_verification_and_promotion(repo):
    registry_with_person("A1")
    registry_with_person("adjudicated")
    _silver("doc_002")
    with pytest.raises(store.StoreError):
        store.start_from_silver("A2", "doc_002")
    store.start_from_silver("A1", "doc_002")
    a1 = store.load("A1", "doc_002")
    assert a1.entities[0].provenance.origin == "silver"
    store.submit("A1", "A1", "doc_002")
    with pytest.raises(store.StoreError) as e:   # silver entity not verified yet
        store.promote("doc_002", source="A1", verified_by="AB", verified_date="2026-10-01")
    assert e.value.code == "unverified_silver_entity"
    # verify in a fresh pass: accept the silver entity
    st = store.load_status("A1", "doc_002")
    write_canonical(store.status_path("A1", "doc_002"), {**st, "submitted": False})
    ok = a1.entities[0].model_copy(update={"provenance": a1.entities[0].provenance.model_copy(
        update={"adjudication": "verified_silver", "verified_by": "AB", "verified_date": "2026-10-01"})})
    store.save("A1", "A1", "doc_002", a1.model_copy(update={"entities": [ok]}), a1.revision)
    store.submit("A1", "A1", "doc_002")
    with pytest.raises(store.StoreError):
        store.promote("doc_002", source="A1", verified_by="", verified_date="2026-10-01")
    out = store.promote("doc_002", source="A1", verified_by="AB", verified_date="2026-10-01")
    gold = store.load("adjudicated", "doc_002")
    assert out["revision"] == 1 and gold.annotator == "adjudicated" and gold.entities[0].provenance.verified_by == "AB"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _req(port, method, path, *, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                               headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            ct = resp.headers.get("content-type", "")
            raw = resp.read()
            return resp.status, (json.loads(raw) if "json" in ct else raw)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_live_api(repo):
    import uvicorn
    from redactor.api.app import create_app
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        assert _req(port, "GET", "/health") == (200, {"ok": True, "network_guard": True})
        code, docs = _req(port, "GET", "/documents")
        assert code == 200 and [d["document_id"] for d in docs] == ["doc_001", "doc_002", "doc_003"]
        assert "original_filename" not in json.dumps(docs) and "source_path" not in json.dumps(docs)
        assert _req(port, "GET", "/annotations/A2/doc_003", headers={"X-Annotator": "A1"})[0] == 403
        code, got = _req(port, "GET", "/annotations/A1/doc_002", headers={"X-Annotator": "A1"})
        assert code == 200 and got["stored_revision"] == 0 and not got["readonly"]
        registry_with_person("A1")
        got["annotation"]["entities"] = [json.loads(entity("doc_002", "Wren Ashdale").model_dump_json(by_alias=True))]
        code, out = _req(port, "PUT", "/annotations/A1/doc_002", body={"base_revision": 0, "annotation": got["annotation"]},
                         headers={"X-Annotator": "A1"})
        assert code == 200 and out["revision"] == 1
        code, out = _req(port, "PUT", "/annotations/A1/doc_002", body={"base_revision": 0, "annotation": got["annotation"]},
                         headers={"X-Annotator": "A1"})
        assert code == 409 and out["error"] == "revision_conflict"
        assert _req(port, "GET", "/candidates/doc_001", headers={"X-Annotator": "A1"})[0] == 403
        code, cand = _req(port, "GET", "/candidates/doc_002", headers={"X-Annotator": "A1"})
        assert code == 200 and any(s["entity_type"] == "PERSON" for s in cand["spans"])
        # §9.5: the default candidates are the baseline + Presidio union (baseline alone if Presidio is missing)
        assert cand["detector"] in ("baseline+presidio", "baseline")
        assert cand["detector"] == "baseline" or {s["detector"].split("@")[0] for s in cand["spans"]} >= {"baseline", "presidio"}
        code, png = _req(port, "GET", "/documents/doc_002/pages/1/image?dpi=60")
        assert code == 200 and png[:8] == b"\x89PNG\r\n\x1a\n"
        assert _req(port, "GET", "/health", headers={"Host": "evil.example"})[0] == 403
        assert _req(port, "GET", "/docs")[0] == 404          # no CDN-backed Swagger UI
    finally:
        server.should_exit = True
        t.join(timeout=10)
