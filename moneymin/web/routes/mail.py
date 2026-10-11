"""mail routes: existing behavior with explicit dependencies."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('hostinger_mail', 'jsonify', 'request')


def mail_cleanup_status(_services):
    from ...mail_cleanup import CLEANUP
    result = CLEANUP.snapshot()
    result["profiles"] = [{"id": p["id"], "name": p.get("name") or "Caixa Hostinger"}
                          for p in _services.hostinger_mail.configured_connections() if p.get("mailbox_id")]
    return _services.jsonify(result)

def mail_cleanup_preview(_services):
    from ...mail_cleanup import CLEANUP, CleanupError
    body = _services.request.get_json(silent=True) or {}
    profiles = [p for p in _services.hostinger_mail.configured_connections()
                if p["id"] == body.get("profile_id") and p.get("mailbox_id")]
    if len(profiles) != 1:
        return _services.jsonify({"error": "Selecione uma caixa Hostinger configurada."}), 400
    try:
        return _services.jsonify(CLEANUP.start(profiles[0])), 202
    except CleanupError as exc:
        return _services.jsonify({"error": str(exc)}), 409

def mail_cleanup_apply(_services):
    from ...mail_cleanup import CLEANUP, CleanupError
    body = _services.request.get_json(silent=True) or {}
    if body.get("confirmed") is not True:
        return _services.jsonify({"error": "Revise e confirme os e-mails que irão para a lixeira."}), 400
    try:
        return _services.jsonify(CLEANUP.apply(body.get("id"), body.get("uids"))), 202
    except CleanupError as exc:
        return _services.jsonify({"error": str(exc)}), 409

def mail_cleanup_stop(_services):
    from ...mail_cleanup import CLEANUP
    CLEANUP.cancel.set()
    return _services.jsonify({"ok": True})
