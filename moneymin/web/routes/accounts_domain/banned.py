"""Route handlers grouped by account workflow."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('BALANCES_RUNNER', '_PERSISTENCE_LOCK', '_banned_withdraw_eligibility', '_confirmed_available_balance', '_crowtado_creds', '_remember_banned_withdraw_result', '_saved_account_password', '_withdraw_bulk_snapshot', '_withdraw_once', 'account_issue', 'banned_monitor', 'banned_store', 'config', 'crowtado', 'jsonify', 'request')


def banned_monitor_snapshot(_services):
    archive = _services.banned_store.load(_services.config.DATA_DIR / "banned_accounts.json", {"accounts": []})
    passwords = _services._crowtado_creds()
    rows = []
    for row in archive.get("accounts", []):
        eligible, reason = _services._banned_withdraw_eligibility(row)
        email = str(row.get("email") or "").strip().casefold()
        rows.append({"email": row["email"], "banned_at": row.get("banned_at") or row.get("removed_at"),
                     "has_password": bool(row.get("password") or _services._saved_account_password(email, passwords)),
                     "monitor": row.get("monitor", {}),
                     "reason": row.get("reason", "Restrição confirmada pela plataforma."),
                     "stage": row.get("stage", ""),
                     "diagnostic": row.get("diagnostic", {}),
                     "withdraw_eligible": eligible, "withdraw_reason": reason})
    return _services.jsonify({"accounts": rows, "runner": _services.banned_monitor.snapshot()})

def banned_account_password(_services):
    """Revela sob demanda a credencial local de uma conta arquivada."""
    body = _services.request.get_json(silent=True) or {}
    email = str(body.get("email") or "").strip().casefold()
    if not email:
        return _services.jsonify({"error": "informe a conta banida"}), 400
    with _services._PERSISTENCE_LOCK:
        archive = _services.banned_store.load(_services.config.DATA_DIR / "banned_accounts.json", {"accounts": []})
        row = next((item for item in archive.get("accounts", [])
                    if str(item.get("email") or "").strip().casefold() == email), None)
        if row is None:
            return _services.jsonify({"error": "conta não encontrada no registro de banidas"}), 404
        password = row.get("password") or _services._saved_account_password(email)
    if not password:
        return _services.jsonify({"error": "esta conta não possui senha salva"}), 404
    response = _services.jsonify({"email": email, "password": password})
    response.headers["Cache-Control"] = "no-store"
    return response

def request_banned_withdraw(_services):
    """Solicita saque Crowtado de conta arquivada, sem autenticar no Minute."""
    body = _services.request.get_json(silent=True) or {}
    email = str(body.get("email") or "").strip().casefold()
    if not email:
        return _services.jsonify({"error": "informe a conta banida"}), 400
    with _services._PERSISTENCE_LOCK:
        archive = _services.banned_store.load(_services.config.DATA_DIR / "banned_accounts.json", {"accounts": []})
        row = next((item for item in archive.get("accounts", [])
                    if str(item.get("email") or "").strip().casefold() == email), None)
        row = dict(row) if row is not None else None
    if row is None:
        return _services.jsonify({"error": "conta não está no registro de banidas"}), 404
    eligible, reason = _services._banned_withdraw_eligibility(row)
    if not eligible:
        return _services.jsonify({"error": reason}), 400
    if _services.banned_monitor.snapshot().get("state") == "running" or _services.BALANCES_RUNNER.running:
        return _services.jsonify({"error": "aguarde a consulta de saldos terminar"}), 409
    if _services._withdraw_bulk_snapshot()["state"] == "running":
        return _services.jsonify({"error": "aguarde o saque em lote terminar"}), 409
    try:
        balance = _services.crowtado.consultar_saldo_api(email, row["password"])
    except (_services.crowtado.CrowtadoError, OSError, TimeoutError, ValueError) as exc:
        reason = _services.account_issue(email, exc, stage="Consulta de saldo Crowtado")["reason"]
        message = f"não foi possível confirmar o saldo na Crowtado: {reason}"
        try:
            _services._remember_banned_withdraw_result(email, "balance_error", message, balance_error=True)
        except (OSError, _services.banned_store.BannedStoreError):
            pass
        return _services.jsonify({"error": message}), 400
    if not _services._confirmed_available_balance(balance):
        try:
            _services._remember_banned_withdraw_result(
                email, "no_balance", "O saldo aprovado deve ser superior a US$ 25,00 para saque na Crowtado.", balance=balance)
        except (OSError, _services.banned_store.BannedStoreError):
            pass
        return _services.jsonify({"error": "o saldo aprovado deve ser superior a US$ 25,00 para saque na Crowtado"}), 400
    result, status = _services._withdraw_once(email, row["password"])
    provider_status = str((result.get("result") or {}).get("status") or "")
    if provider_status or not result.get("ok"):
        try:
            _services._remember_banned_withdraw_result(
                email, provider_status or "request_failed",
                result.get("message") if provider_status else
                "Não foi possível solicitar o saque; atualize o saldo antes de tentar novamente.",
                balance=balance)
        except (OSError, _services.banned_store.BannedStoreError):
            pass  # A solicitação externa já ocorreu; não a repetir por falha local.
    return _services.jsonify(result), status

def refresh_banned_monitor(_services):
    with _services._PERSISTENCE_LOCK:
        archive = _services.banned_store.load(_services.config.DATA_DIR / "banned_accounts.json", {"accounts": []})
        rows = archive.get("accounts", [])
        if not rows:
            return _services.jsonify({"error": "Não há contas banidas registradas."}), 400
        def save(email, result):
            with _services._PERSISTENCE_LOCK:
                path = _services.config.DATA_DIR / "banned_accounts.json"
                current = _services.banned_store.load(path, {"accounts": []})
                for row in current.get("accounts", []):
                    if row.get("email", "").casefold() == email.casefold():
                        row["monitor"] = result
                _services.banned_store.save(path, current)
        try:
            _services.banned_monitor.start(rows, save)
        except RuntimeError as exc:
            return _services.jsonify({"error": str(exc)}), 409
    return _services.jsonify(_services.banned_monitor.snapshot()), 202

def banned_accounts(_services):
    with _services._PERSISTENCE_LOCK:
        path = _services.config.DATA_DIR / "banned_accounts.json"
        archive = _services.banned_store.load(path, {"schema": 1, "accounts": []})
        passwords = _services._crowtado_creds()
        changed = False
        for row in archive.get("accounts", []):
            email = str(row.get("email", "")).strip().casefold()
            for key, value in (("password", row.get("password") or _services._saved_account_password(email, passwords)),
                               ("banned_at", row.get("banned_at") or row.get("removed_at"))):
                if key not in row or row[key] != value:
                    row[key] = value
                    changed = True
        if changed:
            _services.banned_store.save(path, archive)
        response = _services.jsonify(archive)
        response.headers["Cache-Control"] = "no-store"
        return response
