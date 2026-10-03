"""Candidate adapters (plan §11 C1) on synthetic text: label mapping, determinism, offline operation.
All names and numbers are invented."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from redactor.core.types import BBox, DocumentText, PageExtraction, RawWord, build_page_text
from redactor.detectors.adapters.common import AdapterBase, Raw, repo_path, resolve_overlaps, utf16_to_cp

LINES = ["Re: Ms Wren Ashdale, 12 Fernhill Road Sampleton QLD 4999",
         "Call 0491 570 156 or wren.ashdale@example.com about Medicare 2123 45670 1.",
         "She was seen by Dr Tamsin Hollow at Brookvale Hospital on 14 February 2024."]


def doc():
    raw = []
    for li, line in enumerate(LINES):
        x = 50.0
        for w in line.split(" "):
            raw.append(RawWord(w, BBox(x, 80 + 18 * li, x + 6 * len(w), 92 + 18 * li), 95.0, 1, li))
            x += 6 * len(w) + 5
    text, words = build_page_text(raw)
    return DocumentText("doc_1", {1: PageExtraction("doc_1", 1, "text_layer.test#1", "text_layer", 595.0, 842.0, words, text, {})},
                        {"pdf.title": "Report for Wren Ashdale"})


class Fake(AdapterBase):
    name = "fake"
    label_map_name = "presidio"

    def engine_fingerprint(self):
        return {"fake": 1}

    def analyze(self, texts):
        out = []
        for t in texts:
            i = t.find("Wren Ashdale")
            out.append([Raw(i, i + 12, "PERSON", 0.9), Raw(i, i + 4, "NRP", 0.5), Raw(0, 2, "WEIRD_LABEL", 0.4)] if i >= 0 else [])
        return out


def test_shared_adapter_mapping_overlaps_boxes_and_order():
    a = Fake()
    spans = a.detect(doc())
    assert [s.entity_type for s in spans if s.page == 1] == ["OTHER", "PERSON"]
    assert a.stats() == {"unmapped_labels": {"WEIRD_LABEL": 2}, "dropped_overlaps": 2}
    person = next(s for s in spans if s.page == 1 and s.entity_type == "PERSON")
    assert person.text == "Wren Ashdale" and person.bboxes and person.native_type == "PERSON" and person.detector == "fake@0.1.0"
    assert any(s.field == "pdf.title" and s.text == "Wren Ashdale" for s in spans)
    assert a.fingerprint() == Fake().fingerprint()
    raws = [Raw(0, 5, "A", 0.5), Raw(3, 9, "B", 0.5), Raw(10, 12, "C", None)]
    assert resolve_overlaps(raws) == resolve_overlaps(list(reversed(raws))) == [Raw(3, 9, "B", 0.5), Raw(10, 12, "C", None)]
    assert utf16_to_cp("a\U0001F600b", [0, 1, 3, 4]) == [0, 1, 2, 3]


@pytest.mark.skipif(shutil.which("node") is None or not (repo_path("sidecars", "openredaction", "node_modules", "openredaction").exists()),
                    reason="openredaction sidecar not installed")
def test_openredaction_sidecar_offline_deterministic_and_leaves_no_files():
    from redactor.detectors.adapters.openredaction_adapter import OpenRedactionAdapter
    d = repo_path("sidecars", "openredaction")
    r = subprocess.run([shutil.which("node"), str(d / "runner.js"), "--selftest-network"], capture_output=True, text=True, cwd=str(d))
    assert json.loads(r.stdout) == {"blocked": {"fetch": True, "socket": True, "https": True, "dns": True}}
    a = OpenRedactionAdapter()
    s1, s2 = a.detect(doc()), OpenRedactionAdapter().detect(doc())
    assert s1 == s2 and s1
    types = {s.entity_type for s in s1}
    assert {"PHONE", "MEDICARE"} <= types
    # "as shipped": quality is what the evaluation measures (e.g. the e-mail here is missed), not asserted
    assert not (d / ".openredaction").exists()      # the learning store was never written


def test_presidio_au_recognizers_active_ner_only_and_deterministic():
    pytest.importorskip("presidio_analyzer")
    from redactor.detectors.adapters.presidio_adapter import PresidioAdapter
    from redactor.security import netguard
    a = PresidioAdapter({"mode": "default_au"})
    recs = a.recognizers()
    assert {"AuAbnRecognizer", "AuAcnRecognizer", "AuTfnRecognizer", "AuMedicareRecognizer"} <= set(recs)
    s1 = a.detect(doc())
    assert s1 == PresidioAdapter({"mode": "default_au"}).detect(doc())
    assert netguard.is_installed()
    assert any(s.entity_type == "PERSON" and "Ashdale" in s.text for s in s1)
    assert any(s.entity_type == "EMAIL" for s in s1)
    ner = PresidioAdapter({"mode": "ner_only"})
    assert ner.recognizers() == ["SpacyRecognizer"] and ner.fingerprint() != a.fingerprint()
    assert all(s.entity_type in {"PERSON", "LOCATION", "ORGANIZATION", "DATE", "NATIONALITY", "AGE", "OTHER"} for s in ner.detect(doc()))


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker not available")
def test_philter_runs_without_network_and_maps_types():
    from redactor.detectors.adapters import philter_adapter as ph
    got = subprocess.run(["docker", "image", "inspect", ph.IMAGE, "--format", "{{.Id}}"], capture_output=True, text=True)
    if got.returncode != 0:
        pytest.skip("pinned Philter image not pulled")
    a = ph.PhilterAdapter({"policy": "au_extended"})
    spans = a.detect(doc())
    name = a._container()
    mode = subprocess.run(["docker", "inspect", name, "--format", "{{.HostConfig.NetworkMode}}"], capture_output=True, text=True).stdout.strip()
    assert mode == "none"
    assert spans and spans == ph.PhilterAdapter({"policy": "au_extended"}).detect(doc())
    assert any(s.entity_type == "EMAIL" for s in spans) or any(s.entity_type == "PERSON" for s in spans)
