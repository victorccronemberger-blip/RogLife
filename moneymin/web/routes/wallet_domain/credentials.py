"""Route handlers grouped by account workflow."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('BALANCES_RUNNER', '_PAYOUT_OPERATION_LOCK', '_list_accounts', '_payout_method_snapshot', '_save_crowtado_cred', '_withdraw_bulk_snapshot', 'account_bans', 'account_issue', 'crowtado', 'jsonify', 'org_policy', 'request')


def put_balance_credentials(_services):
    if (_services.BALANCES_RUNNER.running or _services._withdraw_bulk_snapshot()["state"] == "running"
            or _services._payout_method_snapshot()["state"] == "running" or _services._PAYOUT_OPERATION_LOCK.locked()):
        return _services.jsonify({"error": "aguarde a operação da Carteira terminar antes de alterar o acesso"}), 409
    body = _services.request.get_json(silent=True) or {}
    email = body.get("email", "")
    password = body.get("password", "")
    if not isinstance(email, str) or not isinstance(password, str):
        return _services.jsonify({"error": "email e senha devem ser texto", "code": "invalid_credentials"}), 400
    email = email.strip()
    if not email or not password:
        return _services.jsonify({"error": "informe email e senha"}), 400
    try:
        _services.account_bans.require_not_banned(email)
    except ValueError as exc:
        return _services.jsonify({"error": str(exc)}), 400
    configured = {a["email"] for a in _services._list_accounts()}
    if email not in configured:
        return _services.jsonify({"error": "essa identidade não está conectada ao QMoney"}), 404
    if _services.org_policy.account_kind(email) == "claru":
        return _services.jsonify({"error": "contas Claru não precisam de acesso Crowtado"}), 400
    try:
        _services.crowtado.login(email, password)
    except (_services.crowtado.CrowtadoError, RuntimeError, OSError) as exc:
        issue = _services.account_issue(email, exc, stage="Login Crowtado")
        return _services.jsonify({
            "error": issue["reason"] + " " + issue["action"],
            "code": "crowtado_login_failed", "issue": issue,
        }), 400
    try:
        _services._save_crowtado_cred(email, password)
    except (OSError, ValueError):
        return _services.jsonify({"ok": False, "partial": True, "code": "local_credential_save_failed",
                        "error": "O acesso Crowtado foi confirmado, mas a senha não foi salva. Confira o armazenamento local antes de tentar novamente."}), 500
    return _services.jsonify({
        "ok": True,
        "email": email,
        "message": "Acesso ao Crowtado confirmado e salvo.",
    })
