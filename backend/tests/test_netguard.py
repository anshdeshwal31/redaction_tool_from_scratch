"""The network guard blocks non-loopback connections and allows loopback (plan §2.4, §12)."""

from __future__ import annotations

import socket
import threading

import pytest

from redactor.security import netguard


def test_guard_blocks_non_loopback():
    with netguard.guarded():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(netguard.NetworkBlocked):
                s.connect(("93.184.216.34", 80))
            with pytest.raises(netguard.NetworkBlocked):
                socket.create_connection(("example.com", 443), timeout=1)
        finally:
            s.close()


def test_guard_allows_loopback():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    t = threading.Thread(target=lambda: server.accept()[0].close(), daemon=True)
    t.start()
    with netguard.guarded():
        c = socket.create_connection(("127.0.0.1", port), timeout=2)
        c.close()
    server.close()


def test_offline_env_set():
    with netguard.guarded():
        import os
        assert os.environ["HF_HUB_OFFLINE"] == "1" and os.environ["DO_NOT_TRACK"] == "1"
