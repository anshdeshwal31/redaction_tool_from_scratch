"""Outputs and re-identification on synthetic data (plan §4.12, §12 "Outputs"). All names are invented."""

from __future__ import annotations

import json

import pytest

from redactor.core.canonical import canonical_json, write_canonical
from redactor.core.types import DocumentText, EntitySpan
from redactor.evaluation import outputs as e9
from redactor.extraction.text_layer import extract
from redactor.output import export as ox
from redactor.output import pdf as pdfmod
from redactor.output.models import validate_document
from redactor.output.render import PAGE_SEPARATOR, render_markdown, render_text
from redactor.paths import fixtures_dir
from redactor.reidentify.core import build_index, reidentify
from redactor.replacement.engine import pseudonymize
from redactor.replacement.pools import load_pools
from redactor.replacement.vault import Vault
from redactor.taxonomy import load_policy, load_taxonomy

POLICY, TAX = load_policy(), load_taxonomy()
PDF = fixtures_dir() / "syn_born_digital.pdf"


SPANS = [("Wren Ashdale", "PERSON", 0, ()), ("14/02/1979", "DATE_OF_BIRTH", 0, ()), ("WC7654321", "CLAIM_NUMBER", 0, ()),
         ("Ashdale", "PERSON", 1, ()), ("12 Fernhill Road Sampleton QLD 4999", "ADDRESS", 0, ()), ("0491 570 156", "PHONE", 0, ()),
         ("wren.ashdale@example.com", "EMAIL", 0, ()), ("2123 45670 1", "MEDICARE", 0, ()), ("Tamsin Hollow", "PERSON", 0, ()),
         ("Brookvale Medical Centre", "ORGANIZATION", 0, (("role", "medical_practice"),))]


def _spans(pe):
    """Hand-labelled synthetic spans (the n-th occurrence of each surface)."""
    out = []
    for text, typ, nth, attrs in SPANS:
        i = -1
        for _ in range(nth + 1):
            i = pe.text.index(text, i + 1)
        out.append(EntitySpan("doc_1", 1, None, pe.text_source_id, i, i + len(text), text, typ, typ, 1.0, "test@1", "t", (), attrs))
    return sorted(out, key=lambda s: s.sort_key())


def _matter(vault=None):
    doc = DocumentText("doc_1", {1: extract("doc_1", PDF, 1)}, {})
    spans = {"doc_1": _spans(doc.pages[1])}
    vault = vault or Vault.ephemeral()
    res = pseudonymize([doc], spans, policy=POLICY, taxonomy=TAX, vault=vault)
    return doc, res, vault


def _built(doc, res, vault):
    return e9.build_documents(res, {"doc_1": doc}, vault, "matter_t")


def test_json_has_no_original_text_and_the_edit_log_is_complete():
    doc, res, vault = _matter()
    built = _built(doc, res, vault)
    out, priv = built["doc_1"]
    assert validate_document(out) and ox.verify_sha(out)
    blob = canonical_json(out)
    for name in ("Ashdale", "Wren", "Tamsin", "Hollow", "Fernhill", "WC7654321", "2123 45670 1", "0491 570 156"):
        assert name not in blob
    assert all(set(e) <= {"edit_id", "page", "field", "span_id", "new_start", "new_end", "entity_type", "canonical_id", "action",
                          "strategy", "rule_id"} for e in out["edit_log"])
    assert all(c is None or c.startswith("matter_t/") for c in (e["canonical_id"] for e in out["edit_log"]))
    assert e9.edit_log_complete(out, priv, doc, vault.matter_key)
    # tampering with one edit is caught
    bad = json.loads(json.dumps(priv))
    bad["edits"][0]["orig_end"] += 1
    assert not e9.edit_log_complete(out, bad, doc, vault.matter_key)
    m = e9.outputs_metrics(built, {"doc_1": doc}, res, vault, lambda: _built(doc, res, vault))
    assert m["schema_valid"] == m["edit_log_complete"] == m["render_deterministic"] == 1
    assert m["original_text_hits_outside_page_text"] == 0


def test_renders_are_pure_functions_of_the_json():
    doc, res, vault = _matter()
    out, _ = _built(doc, res, vault)["doc_1"]
    t1, t2 = render_text(out), render_text(json.loads(canonical_json(out)))
    assert t1 == t2 and t1.endswith("\n") and out["pages"][0]["text"] in t1
    md = render_markdown(out)
    assert md.startswith("# doc_1\n") and "## Page 1" in md
    two = dict(out, pages=out["pages"] + [dict(out["pages"][0], page=2)])
    assert PAGE_SEPARATOR in render_text(two)


def test_reidentify_round_trip_components_initials_case_and_safe_failure():
    doc, res, vault = _matter()
    idx = build_index(vault, pool_names=set(load_pools().surnames))
    out_text = res.documents["doc_1"].pages[1]
    restored, rep = reidentify(out_text, idx)
    assert "Ashdale" in restored and "Tamsin Hollow" in restored and rep.to_dict()["restored_total"] > 0
    sur = vault.get("surname", "ashdale")
    giv = next(vault.get(k, "wren") for k in ("given:female", "given:unknown", "given:male") if vault.get(k, "wren"))
    cases = {
        f"Dr {sur} called.": "Dr Ashdale called.",
        f"{sur}'s claim": "Ashdale's claim",
        f"{sur.upper()}, {giv}": "ASHDALE, Wren",
        f"{giv.lower()} {sur.lower()}": "wren ashdale",
    }
    for text, want in cases.items():
        assert reidentify(text, idx)[0] == want
    # an initial before a restored surname is restored when the vault's full-name surfaces make it unique
    assert reidentify(f"{giv[0]}. {sur} signed.", idx)[0] == "W. Ashdale signed."
    other = next(c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c != giv[0])
    got, rep = reidentify(f"{other}. {sur} signed.", idx)
    assert got == f"{other}. Ashdale signed." and rep.initials_unresolved == 1     # never a guessed initial
    # one-way tokens stay; unknown names stay and are reported, never invented
    got, rep = reidentify("[TFN] and [NATIONALITY] for Zephyrine Quarrington", idx)
    assert got == "[TFN] and [NATIONALITY] for Zephyrine Quarrington" and rep.one_way_tokens == 2
    # ambiguity: two real values behind one surrogate form are left unchanged
    v2 = Vault.ephemeral()
    v2.put("given:female", "anna", "Bea")
    v2.put("given:male", "anton", "Bea")
    got, rep = reidentify("Bea arrived.", build_index(v2))
    assert got == "Bea arrived." and rep.to_dict()["ambiguous_total"] == 1


def test_e9_reidentification_metrics_hit_their_targets():
    doc, res, vault = _matter()
    built = _built(doc, res, vault)
    pools = load_pools()
    names = set(pools.surnames) | set(pools.female) | set(pools.male) | set(pools.unisex)
    rt = e9.roundtrip_metrics(built, {"doc_1": doc}, res, vault, names)
    assert rt["roundtrip_rate"] == 1.0 and rt["redact_tokens_restored"] == 0
    ds = e9.downstream_metrics(res, vault, names)
    assert ds["identities"] >= 1 and ds["wrong_restoration_rate"] == 0.0
    assert e9.one_way_metrics(vault, TAX)["redact_types_with_plaintext_in_vault"] == 0


def test_burned_pdf_is_clean_deterministic_and_covers_the_spans():
    doc, res, vault = _matter()
    out, _ = _built(doc, res, vault)["doc_1"]
    pdf1, images, man = pdfmod.burn_document(PDF, out, [], dpi=150)
    pdf2, _, _ = pdfmod.burn_document(PDF, out, [], dpi=150)
    assert pdf1 == pdf2
    checks = pdfmod.verify_container(pdf1, "doc_1")
    assert checks["ok"] and checks["pages"] == 1
    assert b"syn_born_digital" not in pdf1 and b"/Producer" not in pdf1 and b"/CreationDate" not in pdf1
    assert man["pages"][0]["boxes_px"]
    # every protected span's box area is black in the burned image
    img = images[0]
    for b in man["pages"][0]["boxes_px"]:
        l, t, r, bt = b
        assert max(img.crop((l, t, r, bt)).getdata()) == 0
    # a span without a word box falls back to its whole line (fail-closed)
    pe = doc.pages[1]
    fb = ox.fallback_boxes(pe, len(pe.text), len(pe.text))
    assert fb and fb[0].x0 == 0.0 and fb[0].x1 == pe.width_pt


def _stage(tmp, monkeypatch, *, allowed: bool, doc, res, vault):
    from redactor import pipeline
    built = e9.build_documents(res, {"doc_1": doc}, vault, "matter_t")
    out, priv = built["doc_1"]
    od = pipeline.outputs_dir("matter_t")
    write_canonical(od / "doc_1.export.json", out)
    write_canonical(od / "private" / "doc_1.edit_log.private.json", {**priv, "regions": []})
    write_canonical(pipeline.state_path("matter_t"), {"documents": ["doc_1"], "export_allowed": {"doc_1": allowed},
                                                      "export_sha256": {"doc_1": out["output_sha256"]},
                                                      "pages": [{"document_id": "doc_1", "page": 1, "state": "PASS" if allowed else "BLOCKED"}]})
    return out


def test_export_is_refused_while_the_gate_is_not_clear_and_rescans_artifacts(data_root, monkeypatch):
    from redactor.output.exporter import export_matter
    doc, res, vault = _matter()
    _stage(data_root, monkeypatch, allowed=False, doc=doc, res=res, vault=vault)
    rep = export_matter("matter_t", formats=["json", "text"], vault=vault)
    assert rep["exported"] == 0 and rep["documents"]["doc_1"]["reason"] == "gate_not_clear"
    assert not (data_root / "exports" / "matter_t").exists()

    _stage(data_root, monkeypatch, allowed=True, doc=doc, res=res, vault=vault)
    clean_ocr = lambda images, dpi: ["Nothing to see here."]          # noqa: E731
    rep = export_matter("matter_t", formats=["json", "text", "md", "pdf"], vault=vault, ocr_fn=clean_ocr,
                        source_pdf=lambda d: PDF, render=lambda p, n, dpi: pdfmod.__dict__["Image"].new("L", (600, 800), 255))
    assert rep["exported"] == 1, rep
    files = sorted(p.name for p in (data_root / "exports" / "matter_t").iterdir())
    assert files == ["doc_1.md", "doc_1.pdf", "doc_1.pseudonymized.json", "doc_1.txt", "private"]

    # the re-OCR of the burned page still shows a real name: the export is refused (fail-closed)
    import shutil
    shutil.rmtree(data_root / "exports")
    leaky = lambda images, dpi: ["Seen by Tamsin Hollow today."]        # noqa: E731
    rep = export_matter("matter_t", formats=["pdf"], vault=vault, ocr_fn=leaky, source_pdf=lambda d: PDF,
                        render=lambda p, n, dpi: pdfmod.__dict__["Image"].new("L", (600, 800), 255))
    assert rep["exported"] == 0 and rep["documents"]["doc_1"]["reason"] == "artifact_leak_scan"
    assert not (data_root / "exports").exists()


def test_api_export_409_and_role_gate(data_root, monkeypatch):
    import threading
    import time
    import uvicorn
    from test_store_api import _free_port, _req
    from redactor.api.app import create_app
    monkeypatch.setenv("REDACTOR_VAULT_PASSPHRASE", "synthetic-test-passphrase")
    doc, res, vault = _matter(Vault.open("matter_t"))     # a real encrypted vault file under the temp data root
    _stage(data_root, monkeypatch, allowed=False, doc=doc, res=res, vault=vault)
    vault.commit()
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        assert _req(port, "POST", "/export/matter_t", body={"formats": ["json"]}, headers={"X-Actor": "stranger"})[0] == 403
        code, body = _req(port, "POST", "/export/matter_t", body={"formats": ["json"]}, headers={"X-Actor": "owner"})
        assert code == 409 and body["detail"]["documents"]["doc_1"]["reason"] == "gate_not_clear"
        assert _req(port, "POST", "/reidentify/matter_t", body={"text": "x"}, headers={"X-Actor": "reviewer"})[0] == 403
        sur = vault.get("surname", "hollow")
        code, body = _req(port, "POST", "/reidentify/matter_t", body={"text": f"Dr {sur} agreed."}, headers={"X-Actor": "owner"})
        assert code == 200 and body["text"] == "Dr Hollow agreed." and body["report"]["restored_total"] == 1
        audit = (data_root / "data" / "audit" / "reidentify.jsonl").read_text(encoding="utf-8")
        assert "Hollow" not in audit and sur not in audit and '"restored_total": 1' in audit
    finally:
        server.should_exit = True


@pytest.mark.skipif(not __import__("shutil").which("docker"), reason="docker not available")
def test_reocr_of_burned_page_finds_no_planted_pii():
    from redactor.extraction.ocr_tesseract import TesseractConfig, image_available
    if not image_available(TesseractConfig.load()):
        pytest.skip("tesseract image not built")
    doc, res, vault = _matter()
    out, _ = _built(doc, res, vault)["doc_1"]
    _pdf, images, _ = pdfmod.burn_document(PDF, out, [], dpi=300)
    texts = pdfmod.tesseract_ocr(images, 300)
    joined = " ".join(texts)
    assert "Fernhill" not in joined and "WC7654321" not in joined and "Hollow" not in joined
