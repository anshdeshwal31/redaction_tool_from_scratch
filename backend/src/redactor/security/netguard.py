"""In-process network guard (plan §2.4).

`install()` patches socket connect so that only loopback and local (AF_UNIX) connections work.
Any other connection raises NetworkBlocked. The pipeline installs it at start-up; a test proves it.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from contextlib import contextmanager

OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "NEXT_TELEMETRY_DISABLED": "1",
    "DO_NOT_TRACK": "1",
}


class NetworkBlocked(ConnectionError):
    pass


_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_original_create_connection = socket.create_connection
_installed = False


def _is_loopback(address) -> bool:
    if isinstance(address, (str, bytes)):  # AF_UNIX path
        return True
    host = address[0] if isinstance(address, tuple) and address else address
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    if host in ("localhost", "", None):
        return True
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False  # hostnames other than localhost are refused (no DNS lookups)


def _guarded_connect(self, address):
    if not _is_loopback(address):
        raise NetworkBlocked("network guard: non-loopback connection refused")
    return _original_connect(self, address)


def _guarded_connect_ex(self, address):
    if not _is_loopback(address):
        raise NetworkBlocked("network guard: non-loopback connection refused")
    return _original_connect_ex(self, address)


def _guarded_create_connection(address, *args, **kwargs):
    if not _is_loopback(address):
        raise NetworkBlocked("network guard: non-loopback connection refused")
    return _original_create_connection(address, *args, **kwargs)


def set_offline_env() -> None:
    for key, value in OFFLINE_ENV.items():
        os.environ.setdefault(key, value)


def install() -> None:
    global _installed
    set_offline_env()
    if _installed:
        return
    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]
    socket.create_connection = _guarded_create_connection  # type: ignore[assignment]
    _installed = True


def uninstall() -> None:
    global _installed
    socket.socket.connect = _original_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _original_connect_ex  # type: ignore[method-assign]
    socket.create_connection = _original_create_connection  # type: ignore[assignment]
    _installed = False


def is_installed() -> bool:
    return _installed


@contextmanager
def guarded():
    was = _installed
    install()
    try:
        yield
    finally:
        if not was:
            uninstall()


def is_installed() -> bool:
    return _installed
