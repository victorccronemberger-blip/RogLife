"""Account-domain implementation; invoked with explicit dependency bindings."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('ACCOUNT_HEALTH_PATH', 'CROWTADO_PW_PATH', '_PERSISTENCE_LOCK', '_invalid_local_state', '_load_account_health_history', '_load_balances', '_load_prefs', '_removed_accounts', '_save_balances', 'account_health', 'config', 'credential_store', 'crowtado', 'load_json_state', 'org_policy', 'registration_state', 'save_json', 'token_store')


def _load_account_health_history(_services) -> dict[str, dict[str, Any]]:
    """Validate every consulted diagnostic before assigning it to an owner."""
    history = _services.load_json_state(_services.ACCOUNT_HEALTH_PATH, {})
    normalized = {}
    for owner, record in history.items():
        key = owner.strip().casefold()
        if not key or key in normalized or not isinstance(record, dict):
            _services._invalid_local_state()
        if "email" in record and (not isinstance(record["email"], str)
                                   or record["email"].strip().casefold() != key):
            _services._invalid_local_state()
        try:
            _services.account_health.validate(record, key)
        except (ValueError, TypeError, RecursionError):
            _services._invalid_local_state()
        for field in ("status", "status_label", "checked_at"):
            if field in record and not isinstance(record[field], str):
                _services._invalid_local_state()
        if "attempts" in record and (type(record["attempts"]) is not int or record["attempts"] < 0):
            _services._invalid_local_state()
        if "last_success_at" in record and record["last_success_at"] is not None and not isinstance(record["last_success_at"], str):
            _services._invalid_local_state()
        for field in ("history_saved", "permanently_removed"):
            if field in record and type(record[field]) is not bool:
                _services._invalid_local_state()
        if "issue" in record:
            issue = record["issue"]
            if not isinstance(issue, dict):
                _services._invalid_local_state()
            if "email" in issue and (not isinstance(issue["email"], str)
                                      or issue["email"].strip().casefold() != key):
                _services._invalid_local_state()
            for field in ("restriction_confirmed", "retryable"):
                if field in issue and type(issue[field]) is not bool:
                    _services._invalid_local_state()
            for field in ("code", "reason", "action", "stage"):
                if field in issue and not isinstance(issue[field], str):
                    _services._invalid_local_state()
        normalized[key] = record
    return normalized

def _list_accounts(_services) -> list[dict[str, Any]]:
    """Contas = token_*.json em secrets/ (sem rede). org_key vem do cache de prefs."""
    prefs = _services._load_prefs()
    health = _services._load_account_health_history()
    org_keys = prefs.get("org_keys", {})
    if not isinstance(org_keys, dict):
        org_keys = {}
    removed = _services._removed_accounts()
    registrations = _services.registration_state.load()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key, (path, data) in _services.token_store.records(_services.config.tokens_dir()).items():
        email = data["email"]
        if str(email).strip().casefold() in removed:
            continue
        key = str(email).strip().casefold()
        if _services.account_health.confirmed_ban(email, health.get(key, {}), registrations.get(key)):
            continue
        if key in registrations and registrations[key]["state"] != "complete":
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "email": email,
            "expires_at": data.get("expires_at", 0),
            "org_key": org_keys.get(email),
            "account_kind": _services.org_policy.account_kind(email),
            "last_check": health.get(key, {}),
            "org_name": {
                _services.config.ORG_KEY: "Datoric", _services.config.CLARU_ORG_KEY: "Claru",
                _services.config.HUB_ORG_KEY: "Hub antigo",
            }.get(org_keys.get(email), "Não verificada"),
        })
    return out

def _remove_account_data(_services, email: str) -> None:
    """Remove caches editáveis ligados à conta (o cadastro histórico fica intacto)."""
    with _services._PERSISTENCE_LOCK:
        # Valide o espelho consultado antes de remover a fonte individual ou
        # regravar outras contas. Corrupção/ambiguidade não significa ausência.
        stored = _services.load_json_state(_services.CROWTADO_PW_PATH, {})
        creds = stored if isinstance(stored, dict) else {}
        matching_creds = [
            key for key in creds
            if str(key).casefold() == email.casefold()
        ]
        _services.credential_store.delete(_services.config.SECRETS_DIR, email)
        for key in matching_creds:
            creds.pop(key, None)
        if matching_creds:
            _services.save_json(_services.CROWTADO_PW_PATH, creds)

        balances = _services._load_balances()
        matching_balances = [
            key for key in balances
            if str(key).casefold() == email.casefold()
        ]
        for key in matching_balances:
            balances.pop(key, None)
        if matching_balances:
            _services._save_balances(balances)
    _services.crowtado.clear_cached_session(email)
