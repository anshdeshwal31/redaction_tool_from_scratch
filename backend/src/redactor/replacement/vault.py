"""Per-matter vault (plan §4.9.5): SQLite under data/vault/, encrypted at rest.

- Lookups are keyed hashes HMAC(matter key, kind | normalised value): no plaintext index.
- SYNTHETIC values are stored AES-GCM encrypted (reversible, for local re-identification);
  REDACT values only as a keyed hash (leak-scannable, never restorable).
- The vault key comes from a passphrase (scrypt, salt in the vault; the passphrase from the OS credential
  store, else REDACTOR_VAULT_PASSPHRASE) or a raw 32-byte key; the matter
  key is random (CSPRNG) in production and stored encrypted. Evaluation uses fixed test keys.
- Mappings are frozen once issued; re-issuing bumps the epoch and marks exports stale.
Nothing from the vault is ever logged.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from ..paths import data_dir
from .keys import TEST_MATTER_KEY, hmac_hex, norm

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v BLOB);
CREATE TABLE IF NOT EXISTS mapping (kind TEXT, lookup TEXT, real_enc BLOB, surrogate TEXT, epoch INTEGER, PRIMARY KEY (kind, lookup));
CREATE TABLE IF NOT EXISTS issued (kind TEXT, surrogate TEXT, PRIMARY KEY (kind, surrogate));
CREATE TABLE IF NOT EXISTS redaction (kind TEXT, lookup TEXT, PRIMARY KEY (kind, lookup));
CREATE TABLE IF NOT EXISTS dob (canonical TEXT PRIMARY KEY, real_enc BLOB, surrogate TEXT, refs TEXT, epoch INTEGER);
CREATE TABLE IF NOT EXISTS review (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, reason TEXT, detail_enc BLOB, epoch INTEGER);
"""
TEST_VAULT_KEY = bytes(range(32))


class VaultError(RuntimeError):
    pass


def vault_path(matter_id: str) -> Path:
    return data_dir() / "vault" / f"{matter_id}.sqlite3"


def _from_credential_store(matter_id: str) -> str | None:
    """Plan §10 Q21 default: the passphrase lives in the OS credential store (Windows Credential Manager)."""
    from ..security import credstore
    if not credstore.supported():
        return None
    try:
        return credstore.load(credstore.target(matter_id))
    except credstore.CredStoreError:
        return None


def key_from_passphrase(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode("utf-8"))


class Vault:
    def __init__(self, conn: sqlite3.Connection, vault_key: bytes, matter_key: bytes):
        self.conn = conn
        self._aes = AESGCM(vault_key)
        self.matter_key = matter_key

    # ------------------------------------------------------------ construction
    @classmethod
    def ephemeral(cls, matter_key: bytes = TEST_MATTER_KEY) -> "Vault":
        """In-memory vault with fixed test keys (evaluation, plan §4.7)."""
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        return cls(conn, TEST_VAULT_KEY, matter_key)

    @classmethod
    def open(cls, matter_id: str, *, passphrase: str | None = None, vault_key: bytes | None = None,
             path: Path | None = None) -> "Vault":
        path = path or vault_path(matter_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        try:
            return cls._open_on(conn, matter_id, passphrase, vault_key)
        except BaseException:
            conn.rollback()         # a failed open leaves nothing behind and no lock on the file
            conn.close()
            raise

    @classmethod
    def _open_on(cls, conn: sqlite3.Connection, matter_id: str, passphrase: str | None, vault_key: bytes | None) -> "Vault":
        conn.executescript(SCHEMA)
        row = conn.execute("SELECT v FROM meta WHERE k='salt'").fetchone()
        if row is None:
            salt = secrets.token_bytes(16)
            conn.execute("INSERT INTO meta VALUES ('salt', ?)", (salt,))
        else:
            salt = row[0]
        if vault_key is None:
            passphrase = passphrase if passphrase is not None else os.environ.get("REDACTOR_VAULT_PASSPHRASE")
            if not passphrase:
                passphrase = _from_credential_store(matter_id)
            if not passphrase:
                raise VaultError("vault needs a passphrase: the OS credential store (`redactor vault set-passphrase`), "
                                 "REDACTOR_VAULT_PASSPHRASE, or an explicit key")
            vault_key = key_from_passphrase(passphrase, salt)
        aes = AESGCM(vault_key)
        row = conn.execute("SELECT v FROM meta WHERE k='matter_key'").fetchone()
        if row is None:
            mk = secrets.token_bytes(32)   # production matter keys are random (§4.9.5)
            nonce = secrets.token_bytes(12)
            conn.execute("INSERT INTO meta VALUES ('matter_key', ?)", (nonce + aes.encrypt(nonce, mk, b"matter_key"),))
            conn.execute("INSERT INTO meta VALUES ('epoch', ?)", (b"1",))
            conn.commit()
        else:
            try:
                mk = aes.decrypt(row[0][:12], row[0][12:], b"matter_key")
            except Exception as exc:  # noqa: BLE001 - wrong key: report the type only
                raise VaultError("wrong vault key") from exc
        return cls(conn, vault_key, mk)

    # ------------------------------------------------------------ helpers
    def _enc(self, value: str, aad: str) -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + self._aes.encrypt(nonce, value.encode("utf-8"), aad.encode("utf-8"))

    def _dec(self, blob: bytes, aad: str) -> str:
        return self._aes.decrypt(blob[:12], blob[12:], aad.encode("utf-8")).decode("utf-8")

    def lookup(self, kind: str, value: str) -> str:
        return hmac_hex(self.matter_key, "LOOKUP", kind, norm(value))

    @property
    def epoch(self) -> int:
        row = self.conn.execute("SELECT v FROM meta WHERE k='epoch'").fetchone()
        return int(row[0]) if row else 1

    def bump_epoch(self) -> int:
        e = self.epoch + 1
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('epoch', ?)", (str(e).encode(),))
        self.conn.commit()
        return e

    # ------------------------------------------------------------ SYNTHETIC mappings
    def get(self, kind: str, value: str) -> str | None:
        row = self.conn.execute("SELECT surrogate FROM mapping WHERE kind=? AND lookup=?", (kind, self.lookup(kind, value))).fetchone()
        return row[0] if row else None

    def put(self, kind: str, value: str, surrogate: str) -> None:
        lk = self.lookup(kind, value)
        cur = self.conn.execute("SELECT surrogate FROM mapping WHERE kind=? AND lookup=?", (kind, lk)).fetchone()
        if cur is not None:
            if cur[0] != surrogate:
                raise VaultError("mappings are frozen once issued (bump the epoch to re-issue)")
            return
        self.conn.execute("INSERT INTO mapping VALUES (?, ?, ?, ?, ?)", (kind, lk, self._enc(value, kind), surrogate, self.epoch))
        self.conn.execute("INSERT OR IGNORE INTO issued VALUES (?, ?)", (kind, surrogate))

    def issued(self, kind: str) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT surrogate FROM issued WHERE kind=?", (kind,))}

    def reverse(self, kind: str, surrogate: str) -> list[str]:
        """Real values behind a surrogate (local re-identification only)."""
        rows = self.conn.execute("SELECT real_enc FROM mapping WHERE kind=? AND surrogate=?", (kind, surrogate)).fetchall()
        return sorted({self._dec(r[0], kind) for r in rows})

    def mappings(self) -> Iterator[tuple[str, str, str]]:
        """(kind, real value, surrogate) — in memory only, for the final leak scan and re-identification."""
        for kind, enc, sur in self.conn.execute("SELECT kind, real_enc, surrogate FROM mapping ORDER BY kind, surrogate"):
            yield kind, self._dec(enc, kind), sur

    # ------------------------------------------------------------ REDACT (one-way)
    def add_redaction(self, kind: str, value: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO redaction VALUES (?, ?)", (kind, self.lookup(kind, value)))

    def is_redacted_value(self, kind: str, value: str) -> bool:
        return self.conn.execute("SELECT 1 FROM redaction WHERE kind=? AND lookup=?", (kind, self.lookup(kind, value))).fetchone() is not None

    # ------------------------------------------------------------ DOB (age-preserving, frozen)
    def get_dob(self, canonical: str) -> tuple[str, str | None, list[str]] | None:
        row = self.conn.execute("SELECT real_enc, surrogate, refs FROM dob WHERE canonical=?", (canonical,)).fetchone()
        return (self._dec(row[0], "dob"), row[1], json.loads(row[2])) if row else None

    def put_dob(self, canonical: str, real_iso: str, surrogate_iso: str | None, refs: list[str]) -> None:
        if self.get_dob(canonical) is not None:
            raise VaultError("DOB surrogate is frozen (bump the epoch to re-issue)")
        self.conn.execute("INSERT INTO dob VALUES (?, ?, ?, ?, ?)", (canonical, self._enc(real_iso, "dob"), surrogate_iso,
                                                                   json.dumps(sorted(refs)), self.epoch))

    # ------------------------------------------------------------ review items
    def add_review(self, kind: str, reason: str, detail: dict[str, Any] | None = None) -> None:
        self.conn.execute("INSERT INTO review (kind, reason, detail_enc, epoch) VALUES (?, ?, ?, ?)",
                          (kind, reason, self._enc(json.dumps(detail or {}, sort_keys=True), "review"), self.epoch))

    def reviews(self) -> list[dict[str, Any]]:
        return [{"kind": k, "reason": r, "detail": json.loads(self._dec(d, "review")), "epoch": e}
                for k, r, d, e in self.conn.execute("SELECT kind, reason, detail_enc, epoch FROM review ORDER BY id")]

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()
