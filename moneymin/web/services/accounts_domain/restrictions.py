"""Account-domain implementation; invoked with explicit dependency bindings."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('_BULK_REGISTER_LOCK', '_BULK_REGISTER_STATE', '_PERSISTENCE_LOCK', '_ban_accounts', '_crowtado_creds', '_load_account_health_history', '_load_prefs', '_mark_bulk_account_removed', '_remove_account_data', '_removed_accounts', '_save_prefs', '_save_registration_batch', '_set_account_removed', 'account_bans', 'account_health', 'account_transfer', 'banned_store', 'campaign_state_lease', 'config', 'credential_store', 'datetime', 'registration_state', 'time', 'token_store')


def on_campaign_restriction(_services, email: str) -> None:
    """Apply the existing confirmed-restriction policy through account services."""
    _services._ban_accounts([{
        "email": email, "restriction_confirmed": True, "stage": "Envio da campanha",
        "reason": "Restrição confirmada pela plataforma durante o envio.",
    }])


def _ban_accounts(_services, issues: list[dict]) -> None:
    """Registra primeiro a restrição, depois remove o acesso local permanentemente."""
    if not issues or any(i.get("restriction_confirmed") is not True for i in issues):
        raise ValueError("A remoção exige restrição confirmada pela plataforma.")
    # The legacy journal migration needs the still-present token to establish
    # ownership. Hold the shared campaign-state lease before persistence locks,
    # matching the global state/store lock order used by uploads and cleanup.
    with _services.campaign_state_lease(), _services._PERSISTENCE_LOCK:
        from moneymin import upload

        # Migration is read-only with respect to remote services and is
        # deliberately before the ban archive, token removal, and account purge.
        # Any corrupt/ambiguous legacy source aborts the ban before access data
        # or its recovery evidence is changed.
        upload.sidecars_dir()
        path = _services.config.DATA_DIR / "banned_accounts.json"
        archive = _services.banned_store.load(path, {"schema": 1, "accounts": []})
        if (not isinstance(archive, dict) or not isinstance(archive.get("accounts"), list)
                or any(not isinstance(row, dict) or not isinstance(row.get("email"), str)
                       for row in archive.get("accounts", []))):
            raise ValueError("Registro de contas banidas inválido; remoção cancelada.")
        records = {row["email"].casefold(): row for row in archive["accounts"]}
        passwords = _services._crowtado_creds()
        for issue in issues:
            email = _services.account_transfer.email_key(issue.get("email"))
            previous = records.get(email, {})
            banned_at = previous.get("banned_at") or previous.get("removed_at") or _services.time.strftime("%Y-%m-%dT%H:%M:%S%z")
            records[email] = {**previous, "email": email, "password": passwords.get(email) or previous.get("password"),
                              "banned_at": banned_at, "removed_at": previous.get("removed_at") or banned_at,
                              "reason": issue.get("reason", "Restrição confirmada pela plataforma."),
                              "stage": issue.get("stage", "Envio"), "restriction_confirmed": True}
            diagnostic = dict(previous.get("diagnostic", {})) if isinstance(previous.get("diagnostic"), dict) else {}
            for key, allowed in (("provider", {"crowtado", "minute"}), ("code", {"restricted"}),
                                 ("provider_error_code", _services.registration_state.PROVIDER_ERROR_CODES),
                                 ("phase", {"browser_setup", "signup_submission", "email_verification", "signup_confirmation"}),
                                 ("blocked_reason", {"user", "device", "uber-device"})):
                value = issue.get(key)
                if isinstance(value, str) and value in allowed:
                    diagnostic[key] = value
            if type(issue.get("http_status")) is int and 400 <= issue["http_status"] <= 599:
                diagnostic["http_status"] = issue["http_status"]
            if type(issue.get("remote_effect_possible")) is bool:
                diagnostic["remote_effect_possible"] = issue["remote_effect_possible"]
            observed_at = issue.get("checked_at")
            if isinstance(observed_at, str):
                try:
                    _services.datetime.datetime.fromisoformat(observed_at)
                except ValueError:
                    pass
                else:
                    diagnostic.setdefault("first_observed_at", observed_at)
                    diagnostic["last_observed_at"] = observed_at
            from moneymin import registration_proxy
            for key in ("registration_step", "checked_at", "detail"):
                if isinstance(issue.get(key), str):
                    diagnostic[key] = registration_proxy.redact(
                        issue[key], records[email].get("password") or "")[:2048]
            registration = _services.registration_state.load().get(email, {})
            if registration:
                diagnostic["steps"] = {name: step["status"]
                    for name, step in registration["steps"].items()}
            if diagnostic:
                records[email]["diagnostic"] = diagnostic
        archive["accounts"] = list(records.values())
        _services.banned_store.save(path, archive)
        for issue in issues:
            email = _services.account_transfer.email_key(issue["email"])
            _services._set_account_removed(email, True)
            _services.token_store.delete(_services.config.SECRETS_DIR, email)
            prefs = _services._load_prefs()
            prefs["selected_accounts"] = [e for e in prefs.get("selected_accounts", []) if e.casefold() != email]
            prefs["org_keys"] = {e: key for e, key in prefs.get("org_keys", {}).items() if e.casefold() != email}
            _services._save_prefs(prefs)
            _services._remove_account_data(email)
        _services.account_bans.purge_local_records({_services.account_transfer.email_key(i["email"]) for i in issues})
        for issue in issues:
            _services._mark_bulk_account_removed(issue["email"])
        with _services._BULK_REGISTER_LOCK:
            if _services._BULK_REGISTER_STATE.get("results"):
                _services._save_registration_batch()

def _reconcile_account_bans(_services) -> list[str]:
    """Archive confirmed diagnoses left in the active store by older versions."""
    def current_candidates():
        health = _services._load_account_health_history()
        registrations = _services.registration_state.load()
        removed = _services._removed_accounts()
        return {
            email: issue for email in (health.keys() | registrations.keys()) - removed
            if (issue := _services.account_health.confirmed_ban(
                email, health.get(email, {}), registrations.get(email)))
        }, registrations

    # Keep the common account-list request cheap and independent of a pending
    # reset when there is nothing to reconcile.
    with _services._PERSISTENCE_LOCK:
        candidates, _registrations = current_candidates()
        if not candidates:
            return []

    # Re-read under the lock order used by _ban_accounts. A health check may
    # have cleared or refreshed a stale restriction since the first snapshot.
    with _services.campaign_state_lease(), _services._PERSISTENCE_LOCK:
        candidates, registrations = current_candidates()
        owners = set(_services.token_store.records(_services.config.tokens_dir()))
        owners.update(email for email in registrations
                      if _services.credential_store.record_path(_services.config.SECRETS_DIR, email).exists())
        issues = [candidates[email] for email in sorted(owners & candidates.keys())]
        if issues:
            _services._ban_accounts(issues)
        return [issue["email"] for issue in issues]

def _archive_checked_ban(_services, email: str, result: dict) -> None:
    issue = _services.account_health.confirmed_ban(email, result)
    if issue:
        _services._ban_accounts([issue])
        result["permanently_removed"] = True
