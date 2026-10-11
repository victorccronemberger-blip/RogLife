"""Account-domain implementation; invoked with explicit dependency bindings."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('Session', '_cache_org_key', 'config', 'org_policy')


def _migrate_account_org(_services, email: str) -> dict[str, Any]:
    kind = _services.org_policy.account_kind(email)
    row = {"email": email, "account_kind": kind}
    if kind == "claru":
        return {**row, "status": "skipped", "message": "Claru: mantida sem aplicar código."}
    session = _services.Session.from_email(email)
    profile = session.ensure_auth()
    before = _services.org_policy.pick_org_key(email, profile.get("organizations") or [])
    target = _services.org_policy.ensure_membership(session, email, profile.get("organizations") or [])
    _services._cache_org_key(email, target)
    return {
        **row, "status": "already" if before else "migrated", "org_key": target,
        "message": ("Já estava na organização nova." if before else "Organização atualizada.")
                   + f" Crowtado · {_services.config.INVITE_CODE}",
    }
