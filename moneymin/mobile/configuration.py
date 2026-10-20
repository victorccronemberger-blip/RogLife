"""Installation-scoped mobile configuration; credentials are never persisted here."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def settings(environ) -> tuple[str, ...]:
    return tuple(environ.get(key, "").strip() for key in (
        "FIREBASE_APP_CHECK_PROJECT_ID", "FIREBASE_APP_CHECK_APP_ID",
        "FIREBASE_APP_CHECK_API_KEY", "FIREBASE_APP_CHECK_DEBUG_TOKEN"))


def configured_bearer(environ) -> str:
    return environ.get("FIREBASE_APP_CHECK_BEARER", "").strip()


def settings_digest(root, values: tuple[str, ...], bearer: str) -> str:
    return hashlib.sha256(
        json.dumps((str(root), values, bool(bearer))).encode()
    ).hexdigest()[:24]


def settings_identity(root, values: tuple[str, ...], bearer: str) -> bytes:
    return hashlib.sha256(json.dumps((str(root), values, bearer)).encode()).digest()


def state_path(data_dir) -> Path:
    return Path(data_dir) / "appcheck_mint_state.json"


def lock_path(data_dir) -> Path:
    return Path(data_dir) / "appcheck_exchange.lock"


def is_paused(data_dir) -> bool:
    try:
        return (Path(data_dir) / "appcheck_pause.flag").exists()
    except OSError:
        return False
