"""Explicit local authentication fixtures for HTTP integration tests.

These helpers provide locally inspectable credentials only. The synthetic JWT
has a future expiry so App Check admission can exercise its normal bearer path;
its signature is intentionally not a real provider credential.
"""
from __future__ import annotations

import base64
import json
import os
import time
from unittest.mock import patch


def _segment(value: dict) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def configure_app_check(test_case, *, now: int | None = None) -> str:
    """Install an explicit, short-lived bearer fixture for one unittest case.

    Production App Check code still evaluates source selection, JWT shape and
    expiry. Callers with a frozen test clock can pass its epoch as ``now``.
    """
    issued_at = int(time.time()) if now is None else int(now)
    token = ".".join((
        _segment({"alg": "RS256", "typ": "JWT"}),
        _segment({"iss": "qmoney-test-fixture", "sub": "fixture", "aud": "fixture",
                  "iat": issued_at, "exp": issued_at + 3600}),
        "fixture-signature",
    ))
    environment = patch.dict(os.environ, {
        "QMONEY_APPCHECK_SOURCE": "bearer",
        "FIREBASE_APP_CHECK_BEARER": token,
    })
    environment.start()
    test_case.addCleanup(environment.stop)
    return token
