"""Presidio candidate (plan §11 C1), "as shipped" apart from two documented settings.

Modes
- `default_au`: Presidio's default recognizer registry for English plus the four Australian recognizers
  (ABN, ACN, TFN, Medicare), added explicitly and checked at startup (they are not loaded by default).
- `ner_only`: only the spaCy NER recognizer.
NLP engine: spaCy `en_core_web_lg` 3.8.0 (pinned wheel in uv.lock), loaded from the installed package;
nothing is downloaded. tldextract (used by the e-mail recognizer) is switched to its bundled public-suffix
snapshot with no fetch and no disk cache, so no network is attempted; the in-process network guard
stays on as well.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..base import register_detector
from .common import AdapterBase, Raw

AU = ("AuAbnRecognizer", "AuAcnRecognizer", "AuTfnRecognizer", "AuMedicareRecognizer")
AU_ENTITIES = {"AU_ABN", "AU_ACN", "AU_TFN", "AU_MEDICARE"}
_ENGINES: dict[tuple, Any] = {}


def _offline_tldextract() -> None:
    import tldextract.tldextract as tx
    if getattr(tx.TLD_EXTRACTOR, "suffix_list_urls", None):
        tx.TLD_EXTRACTOR = tx.TLDExtract(suffix_list_urls=(), cache_dir=None, fallback_to_snapshot=True)


class PresidioAdapter(AdapterBase):
    name = "presidio"
    version = "0.1.0"
    label_map_name = "presidio"

    def __init__(self, config: Mapping[str, Any] | None = None):
        cfg = {"mode": "default_au", "spacy_model": "en_core_web_lg", "score_threshold": 0.0, "language": "en", **dict(config or {})}
        if cfg["mode"] not in ("default_au", "ner_only"):
            raise ValueError("presidio mode must be default_au or ner_only")
        super().__init__(cfg)
        self.name = "presidio" if cfg["mode"] == "default_au" else "presidio_ner"
        self._engine = None

    def engine(self):
        key = (self.config["mode"], self.config["spacy_model"])
        if key in _ENGINES:
            return _ENGINES[key]
        from ...security import netguard
        netguard.install()
        _offline_tldextract()
        from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
        from presidio_analyzer import predefined_recognizers as pr
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        nlp = NlpEngineProvider(nlp_configuration={"nlp_engine_name": "spacy",
                                                   "models": [{"lang_code": "en", "model_name": self.config["spacy_model"]}]}).create_engine()
        registry = RecognizerRegistry(supported_languages=["en"])
        if self.config["mode"] == "default_au":
            registry.load_predefined_recognizers(languages=["en"], nlp_engine=nlp)
            for cls in AU:
                registry.add_recognizer(getattr(pr, cls)())
            got = {e for r in registry.recognizers for e in r.supported_entities}
            missing = AU_ENTITIES - got
            if missing:
                raise RuntimeError(f"Australian recognizers not active: {sorted(missing)}")
        else:
            registry.add_recognizer(pr.SpacyRecognizer(supported_language="en"))
        eng = AnalyzerEngine(registry=registry, nlp_engine=nlp, supported_languages=["en"])
        _ENGINES[key] = eng
        return eng

    def engine_fingerprint(self) -> dict[str, Any]:
        from importlib.metadata import version
        return {"presidio_analyzer": version("presidio-analyzer"), "spacy": version("spacy"),
                "model": f"{self.config['spacy_model']}=={version(self.config['spacy_model'].replace('_', '-'))}"}

    def recognizers(self) -> list[str]:
        return sorted({type(r).__name__ for r in self.engine().registry.recognizers})

    def analyze(self, texts: Sequence[str]) -> list[list[Raw]]:
        eng = self.engine()
        out = []
        for t in texts:
            if not t.strip():
                out.append([])
                continue
            res = eng.analyze(text=t, language=self.config["language"], score_threshold=float(self.config["score_threshold"]))
            out.append([Raw(r.start, r.end, r.entity_type, round(float(r.score), 4)) for r in res])
        return out


@register_detector("presidio")
def _make(config: Mapping[str, Any]) -> PresidioAdapter:
    return PresidioAdapter({**dict(config or {}), "mode": (config or {}).get("mode", "default_au")})


@register_detector("presidio_ner")
def _make_ner(config: Mapping[str, Any]) -> PresidioAdapter:
    return PresidioAdapter({**dict(config or {}), "mode": "ner_only"})
