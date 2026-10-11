"""Route handlers grouped by account workflow."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('BALANCES_RUNNER', '_PAYOUT_OPERATION_LOCK', '_balance_accounts', '_configured_crowtado_creds', '_load_balances', '_on_balance_result', '_payout_method_snapshot', '_withdraw_bulk_snapshot', 'jsonify', 'org_policy', 'request')


def refresh_balances(_services):
    if (_services._withdraw_bulk_snapshot()["state"] == "running"
            or _services._payout_method_snapshot()["state"] == "running"
            or _services._PAYOUT_OPERATION_LOCK.locked()):
        return _services.jsonify({"error": "aguarde o saque em lote terminar"}), 409
    body = _services.request.get_json(silent=True) or {}
    if (not isinstance(body, dict) or not isinstance(body.get("emails", []), list)
            or any(not isinstance(email, str) for email in body.get("emails", []))):
        return _services.jsonify({"error": "informe uma lista de e-mails para consultar"}), 400
    _services._load_balances()
    account_rows, claru_connections = _services._balance_accounts()
    configured = {a["email"] for a in account_rows}
    creds = {e: p for e, p in _services._configured_crowtado_creds().items() if e in configured}
    creds.update({email: "claru-protected-connection" for email in claru_connections})
    emails = [e.strip().casefold() for e in body.get("emails", []) if e.strip()]
    if emails:
        normalized = {e.casefold(): e for e in configured}
        selected = {normalized[e] for e in emails if e in normalized}
        if not selected:
            return _services.jsonify({
                "error": "As contas selecionadas não estão conectadas para consultar saldo.",
            }), 400
        creds = {e: creds[e] for e in selected if e in creds}
    elif not configured:
        return _services.jsonify({
            "error": "Não há contas conectadas para consultar saldo.",
        }), 400
    if not creds:
        if emails and all(_services.org_policy.account_kind(email) == "claru" for email in emails):
            return _services.jsonify({"error": "Conecte o perfil Claru destas contas em Contas → Claru antes de consultar saldo."}), 400
        return _services.jsonify({
            "error": (
                "a identidade está conectada ao Minute, mas o acesso ao "
                "Crowtado ainda não foi informado; use Conectar Crowtado "
                "na linha da conta"
            ),
        }), 400
    try:
        _services.BALANCES_RUNNER.start(creds, _services._on_balance_result)
    except RuntimeError as exc:
        return _services.jsonify({"error": str(exc)}), 409
    return _services.jsonify({"ok": True, "total": len(creds)})

def stop_balance_refresh(_services):
    _services.BALANCES_RUNNER.stop()
    return _services.jsonify({"ok": True, "runner": _services.BALANCES_RUNNER.snapshot()})
