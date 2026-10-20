"""Defaults de teste: VPN/TLS curl não devem derrubar a suíte isolada."""
from __future__ import annotations

import pytest
import os
from unittest.mock import patch

from moneymin import config, vpn


@pytest.fixture(autouse=True)
def _isolate_process_environment():
    # Admission may select a device provider for its current operation. Keep
    # those process defaults inside the test which selected them, including
    # tests which fail before cleaning up their own environment changes.
    with patch.dict(os.environ, dict(os.environ)):
        yield


@pytest.fixture(autouse=True)
def _relax_local_transport_gates(monkeypatch):
    monkeypatch.setattr(vpn, "ENFORCE", False)
    monkeypatch.setattr(config, "REQUIRE_CURL", False)
