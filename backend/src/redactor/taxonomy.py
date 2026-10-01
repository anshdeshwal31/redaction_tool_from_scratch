"""Taxonomy and policy loading (plan §3.7). Configuration, not code: YAML files under config/."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml

from .paths import config_dir

ENTITY_TYPES: tuple[str, ...] = (
    "PERSON", "ORGANIZATION", "ADDRESS", "EMAIL", "PHONE", "URL", "LOCATION",
    "CLAIM_NUMBER", "COURT_FILE_NUMBER", "POLICY_NUMBER", "TFN", "ABN", "ACN", "PASSPORT",
    "DRIVER_LICENCE", "VEHICLE_REGISTRATION", "CENTRELINK_CRN", "ACCOUNT_NUMBER",
    "MEDICARE", "PROVIDER_NUMBER", "AHPRA_REGISTRATION", "IHI", "MEDICAL_RECORD_NUMBER", "DVA_NUMBER",
    "DATE", "AGE", "DATE_OF_BIRTH", "GENDER", "NATIONALITY", "SIGNATURE", "PHOTO", "OTHER",
)
ACTIONS: tuple[str, ...] = ("KEEP", "SYNTHETIC", "REDACT", "REVIEW")
PROTECT_ACTIONS = frozenset({"SYNTHETIC", "REDACT"})
PERSON_ROLES = ("plaintiff", "plaintiff_family", "witness", "co_worker", "treating_practitioner",
                "examining_expert", "legal_representative", "insurer_representative",
                "employer_representative", "court_officer", "other")
ORG_ROLES = ("employer", "insurer", "statutory_body", "court_tribunal", "government", "law_firm",
             "hospital", "medical_practice", "education", "other")
LOCATION_GRANULARITY = ("country", "state", "city", "suburb", "street", "facility", "incident_site")
DATE_ROLES = ("date_of_injury", "examination", "report", "claim", "treatment", "other")


@dataclass(frozen=True)
class Taxonomy:
    taxonomy_id: str
    version: str
    types: Mapping[str, Mapping[str, Any]]
    compatible: tuple[frozenset[str], ...]
    report_buckets: Mapping[str, tuple[str, ...]]

    def group(self, entity_type: str) -> str:
        return self.types.get(entity_type, self.types["OTHER"])["group"]

    def is_pii(self, entity_type: str) -> bool:
        return bool(self.types.get(entity_type, self.types["OTHER"]).get("is_pii", True))

    def validator(self, entity_type: str) -> str | None:
        return self.types.get(entity_type, {}).get("validator")

    def region_only(self, entity_type: str) -> bool:
        return bool(self.types.get(entity_type, {}).get("region_only", False))

    def compatible_types(self, a: str, b: str) -> bool:
        if a == b:
            return True
        return any(a in pair and b in pair for pair in self.compatible)

    def bucket_of(self, entity_type: str) -> str | None:
        for name, types in self.report_buckets.items():
            if entity_type in types:
                return name
        return None


@lru_cache(maxsize=8)
def load_taxonomy(path: str | None = None) -> Taxonomy:
    p = Path(path) if path else config_dir() / "taxonomy.v0.1.yaml"
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    types = raw["types"]
    missing = set(ENTITY_TYPES) ^ set(types)
    if missing:
        raise ValueError(f"taxonomy and code disagree on types: {sorted(missing)}")
    return Taxonomy(
        taxonomy_id=raw["taxonomy_id"], version=str(raw["version"]), types=types,
        compatible=tuple(frozenset(pair) for pair in raw.get("compatible", [])),
        report_buckets={k: tuple(v) for k, v in raw.get("report_buckets", {}).items()},
    )


@dataclass(frozen=True)
class Decision:
    action: str
    strategy: str | None = None
    token: str | None = None
    source: str = "policy"  # policy | override
    rule: str = "default"
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Policy:
    policy_id: str
    version: str
    defaults: Mapping[str, Mapping[str, Any]]
    identifier_types: frozenset[str]
    identifier_strategy: Mapping[str, str]
    rules: tuple[Mapping[str, Any], ...]
    critical: tuple[Mapping[str, Any], ...]
    review_counts_as_leaked: bool

    @property
    def ref(self) -> str:
        return f"{self.policy_id}@{self.version}"

    def decide(self, entity_type: str, role: str | None = None,
               attributes: Mapping[str, Any] | None = None, validator: str | None = None) -> Decision:
        attributes = attributes or {}
        for i, rule in enumerate(self.rules):
            if _matches(rule["when"], entity_type, role, attributes):
                return Decision(rule["action"], rule.get("strategy"), rule.get("token"), "policy", f"rule[{i}]",
                                {k: v for k, v in rule.items() if k not in ("when", "action", "strategy", "token")})
        if entity_type in self.defaults:
            d = self.defaults[entity_type]
            return Decision(d["action"], d.get("strategy"), d.get("token"), "policy", "default",
                            {k: v for k, v in d.items() if k not in ("action", "strategy", "token")})
        if entity_type in self.identifier_types:
            strategy = self.identifier_strategy["checksummed" if validator else "other"]
            return Decision("SYNTHETIC", strategy, None, "policy", "identifier_default")
        return Decision("REVIEW", None, None, "policy", "fallback")

    def is_critical(self, entity_type: str, role: str | None = None) -> bool:
        for crit in self.critical:
            types = crit.get("entity_type", [])
            types = [types] if isinstance(types, str) else types
            if entity_type not in types:
                continue
            roles = crit.get("role")
            if roles is None or (role in roles):
                return True
        return False


def _matches(when: Mapping[str, Any], entity_type: str, role: str | None, attributes: Mapping[str, Any]) -> bool:
    et = when.get("entity_type")
    if et is not None and entity_type not in ([et] if isinstance(et, str) else et):
        return False
    roles = when.get("role")
    if roles is not None and role not in roles:
        return False
    for key, allowed in (when.get("attributes") or {}).items():
        value = attributes.get(key)
        if value is None or str(value) not in [str(a) for a in allowed]:
            return False
    return True


@lru_cache(maxsize=8)
def load_policy(path: str | None = None) -> Policy:
    p = Path(path) if path else config_dir() / "policy.v0.1.yaml"
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    for name in raw["defaults"]:
        if name not in ENTITY_TYPES:
            raise ValueError(f"policy default for unknown type {name}")
    return Policy(
        policy_id=raw["policy_id"], version=str(raw["version"]), defaults=raw["defaults"],
        identifier_types=frozenset(raw.get("identifier_types", [])),
        identifier_strategy=raw.get("identifier_strategy", {"checksummed": "checksum_invalid", "other": "format_preserving"}),
        rules=tuple(raw.get("rules", [])), critical=tuple(raw.get("critical", [])),
        review_counts_as_leaked=bool(raw.get("review_counts_as_leaked", True)),
    )
