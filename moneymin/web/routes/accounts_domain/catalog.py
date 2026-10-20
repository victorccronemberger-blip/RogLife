"""Route handlers grouped by account workflow."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('BALANCES_RUNNER', 'RECOVERY', 'RUNNER', '_BULK_REGISTER_STATE', '_PERSISTENCE_LOCK', '_crowtado_creds', '_list_accounts', '_load_account_health_history', '_load_balances', '_reconcile_account_bans', '_removed_accounts', '_saved_account_password', 'account_health', 'config', 'credential_store', 'jsonify', 'org_policy', 'registration_state', 'request', 'wallet')


def get_accounts(_services):
    if not (_services.RUNNER.running or _services.RECOVERY.running or _services.BALANCES_RUNNER.running
            or _services._BULK_REGISTER_STATE.get("state") == "running"):
        _services._reconcile_account_bans()
    accounts = _services._list_accounts()
    registrations = _services.registration_state.load()
    removed = _services._removed_accounts()
    health = _services._load_account_health_history()
    known = {row["email"].strip().casefold() for row in accounts}
    for email, registration in registrations.items():
        if (email not in known and email not in removed
                and not _services.account_health.confirmed_ban(email, health.get(email, {}), registration)
                and _services.credential_store.record_path(_services.config.SECRETS_DIR, email).exists()):
            accounts.append({"email": email, "expires_at": 0, "org_key": None,
                             "org_name": "Não verificada", "account_kind": _services.org_policy.account_kind(email),
                             "last_check": health.get(email, {}), "has_minute_access": _services.config.token_path(email).exists()})
    # Each row performs a strict primary lookup below. Do not decrypt the
    # entire primary store first, then decrypt those same records again.
    passwords = _services._crowtado_creds(include_individual=False)
    balances = _services._load_balances()
    for account in accounts:
        account.setdefault("has_minute_access", True)
        registration = registrations.get(account["email"].strip().casefold())
        if registration:
            account["registration"] = registration
        account["has_password"] = bool(_services._saved_account_password(account["email"], passwords))
        record = balances.get(account["email"], {})
        account["restriction"] = _services.wallet.restriction(record, _services.wallet.reading(record), account)
        registration_ban = _services.account_health.confirmed_ban(account["email"], account.get("last_check", {}), registration)
        if registration_ban and registration_ban.get("registration_step"):
            sources = registration_ban["provider"].title()
            account["restriction"] = {"code": "restricted", "confirmed": True,
                "label": "Banida · " + sources, "reason": sources + " confirmou banimento durante o cadastro. Verifique os serviços novamente ou solicite regularização ao suporte."}
    return _services.jsonify({"accounts": accounts})

def account_password(_services):
    body = _services.request.get_json(silent=True) or {}
    email = str(body.get("email") or "").strip().casefold()
    if not email:
        return _services.jsonify({"error": "informe a conta"}), 400
    with _services._PERSISTENCE_LOCK:
        active = any(str(account["email"]).strip().casefold() == email for account in _services._list_accounts())
        if not active and (email in _services._removed_accounts() or email not in _services.registration_state.load()):
            return _services.jsonify({"error": "conta não encontrada"}), 404
        password = _services._saved_account_password(email)
    if not password:
        return _services.jsonify({"error": "esta conta não possui senha salva"}), 404
    response = _services.jsonify({"email": email, "password": password})
    response.headers["Cache-Control"] = "no-store"
    return response
