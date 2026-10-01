"""OS credential store for vault passphrases (plan §4.9.5, §10 Q21 default "OS credential store").

Windows Credential Manager through the Win32 API (advapi32 CredWriteW / CredReadW / CredDeleteW via
ctypes, standard library only; no new package). The secret never touches disk in this project, logs or
git. Other platforms: not supported here (the passphrase environment variable still works there).
Target names: "redactor/vault/<matter_id>".
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


class CredStoreError(RuntimeError):
    pass


def supported() -> bool:
    return sys.platform == "win32"


def target(matter_id: str) -> str:
    return f"redactor/vault/{matter_id}"


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR)]


def _api():
    if not supported():
        raise CredStoreError("the OS credential store is supported on Windows only; use REDACTOR_VAULT_PASSPHRASE")
    a = ctypes.WinDLL("advapi32", use_last_error=True)
    a.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIAL), wintypes.DWORD]
    a.CredWriteW.restype = wintypes.BOOL
    a.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_CREDENTIAL))]
    a.CredReadW.restype = wintypes.BOOL
    a.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    a.CredDeleteW.restype = wintypes.BOOL
    a.CredFree.argtypes = [ctypes.c_void_p]
    return a


def store(name: str, secret: str) -> None:
    a = _api()
    blob = secret.encode("utf-16-le")
    buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = _CREDENTIAL(Type=CRED_TYPE_GENERIC, TargetName=name, CredentialBlobSize=len(blob),
                       CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)), Persist=CRED_PERSIST_LOCAL_MACHINE,
                       UserName="redactor")
    if not a.CredWriteW(ctypes.byref(cred), 0):
        raise CredStoreError(f"CredWriteW failed (error {ctypes.get_last_error()})")


def load(name: str) -> str | None:
    a = _api()
    p = ctypes.POINTER(_CREDENTIAL)()
    if not a.CredReadW(name, CRED_TYPE_GENERIC, 0, ctypes.byref(p)):
        return None
    try:
        c = p.contents
        return bytes(c.CredentialBlob[:c.CredentialBlobSize]).decode("utf-16-le")
    finally:
        a.CredFree(p)


def delete(name: str) -> bool:
    return bool(_api().CredDeleteW(name, CRED_TYPE_GENERIC, 0))
