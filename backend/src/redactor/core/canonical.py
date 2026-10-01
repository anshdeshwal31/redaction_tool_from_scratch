"""Canonical JSON and hashing (plan §3.1 principle 6, §4.7 code rules).

Canonical form: sorted keys, UTF-8 (no ASCII escaping), floats rounded to a fixed number of
decimals, tuples as lists, no NaN/Infinity. The same object always produces the same bytes.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any

FLOAT_DECIMALS = 4


def normalize(obj: Any, ndigits: int = FLOAT_DECIMALS) -> Any:
    """Return a JSON-ready structure with deterministic floats and plain containers."""
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            raise ValueError("NaN/Infinity cannot be serialized canonically")
        value = round(obj, ndigits)
        return 0.0 if value == 0 else value
    if isinstance(obj, Enum):
        return normalize(obj.value, ndigits)
    if hasattr(obj, "model_dump"):
        return normalize(obj.model_dump(mode="json", by_alias=True), ndigits)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        if hasattr(obj, "to_dict"):
            return normalize(obj.to_dict(), ndigits)
        return normalize(dataclasses.asdict(obj), ndigits)
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if not isinstance(key, str):
                key = str(key)
            out[key] = normalize(value, ndigits)
        return out
    if isinstance(obj, (list, tuple)):
        return [normalize(v, ndigits) for v in obj]
    if isinstance(obj, (set, frozenset)):
        items = [normalize(v, ndigits) for v in obj]
        return sorted(items, key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False))
    if isinstance(obj, Path):
        return obj.as_posix()
    raise TypeError(f"cannot canonicalize object of type {type(obj).__name__}")


def canonical_json(obj: Any, *, indent: int | None = None) -> str:
    if indent is None:
        return json.dumps(normalize(obj), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return json.dumps(normalize(obj), sort_keys=True, ensure_ascii=False, indent=indent, allow_nan=False)


def canonical_bytes(obj: Any) -> bytes:
    return canonical_json(obj).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha256_obj(obj: Any) -> str:
    return sha256_hex(canonical_bytes(obj))


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def write_canonical(path: str | Path, obj: Any, *, indent: int = 2) -> str:
    """Write canonical JSON atomically (LF newlines, trailing newline). Returns the file's sha256."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = canonical_json(obj, indent=indent) + "\n"
    data = text.encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_", suffix=path.suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return sha256_hex(data)


def read_json(path: str | Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def short(hexdigest: str, n: int = 12) -> str:
    return hexdigest[:n]
