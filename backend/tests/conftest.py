"""Test configuration: every test runs against synthetic data under a temporary data root."""

from __future__ import annotations

import pytest


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("REDACTOR_DATA_ROOT", str(tmp_path))
    return tmp_path
