"""Explicit provider assignments for existing accounts, independent of email domain.

These local records declare the intended provider, not verified remote membership.
They contain no credentials and do not change the account's login identity.
"""
from __future__ import annotations

import hashlib

from . import config, credential_store
from .atomic_io import JsonStateError, load_json_state, save_json


def _path(email: str):
    digest = hashlib.sha256(email.encode("utf-8")).hexdigest()
    return config.DATA_DIR / "account_providers" / f"{digest}.json"


def assigned_kind(email: str) -> str | None:
    key = str(email).strip().casefold()
    row = load_json_state(_path(key), None)
    if row is None:
        return None
    if (set(row) != {"schema", "email", "kind"}
            or type(row["schema"]) is not int or row["schema"] != 1
            or row["email"] != key or row["kind"] not in ("claru", "crowtado")):
        raise JsonStateError("A identificação local do provedor está inválida e foi preservada.")
    return row["kind"]


def assign_kind(email: str, kind: str) -> None:
    """Record an explicitly chosen provider without authentication or remote writes."""
    key = credential_store.email_key(email)
    if kind not in ("claru", "crowtado"):
        raise ValueError("Provedor de conta inválido.")
    assigned_kind(key)  # Do not overwrite an unreadable or mismatched record.
    save_json(_path(key), {"schema": 1, "email": key, "kind": kind})
