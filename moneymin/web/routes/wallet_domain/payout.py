"""Route handlers grouped by account workflow."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('BALANCES_RUNNER', '_PAYOUT_METHOD_LOCK', '_PAYOUT_METHOD_STATE', '_PAYOUT_OPERATION_LOCK', '_configured_crowtado_creds', '_finish_wise_cleanup', '_list_accounts', '_payout_method_run', '_wise_cleanup_snapshot', '_withdraw_bulk_snapshot', 'jsonify', 'org_policy', 're', 'request', 'threading')


def finish_pending_wise_cleanup(_services):
    """Retoma apenas a remoção Wise e a preferência Dots, inclusive após reiniciar."""
    if not _services._PAYOUT_OPERATION_LOCK.acquire(blocking=False):
        return _services.jsonify({"error": "aguarde a operação atual terminar"}), 409
    try:
        pending = _services._wise_cleanup_snapshot()
        if not pending.get("pending"):
            return _services.jsonify({"ok": True, "message": "não há limpeza Wise pendente"})
        if pending.get("error"):
            return _services.jsonify({"error": pending["error"]}), 409
        email = pending.get("email")
        password = _services._configured_crowtado_creds().get(email)
        if not email or not password:
            return _services.jsonify({"error": "reconecte o acesso da conta pendente para concluir a limpeza Wise"}), 409
        result = _services._finish_wise_cleanup(email, password)
        if result["cleanupPending"]:
            return _services.jsonify({"error": "limpeza ainda não confirmada; saques continuam bloqueados", "result": result}), 409
        return _services.jsonify({"ok": True, "message": "Wise desvinculada e Dots confirmado. Nenhum saque foi solicitado.",
                        "result": result})
    finally:
        _services._PAYOUT_OPERATION_LOCK.release()

def apply_all_payout_methods(_services):
    if _services._wise_cleanup_snapshot().get("pending"):
        return _services.jsonify({"error": "conclua a limpeza Wise pendente antes de configurar métodos"}), 409
    body = _services.request.get_json(silent=True) or {}
    method = str(body.get("method") or "").strip().lower()
    legal_name = str(body.get("legal_name") or "").strip()
    destination_email = str(body.get("destination_email") or "").strip()
    if method not in {"dots", "paypal", "wise"}:
        return _services.jsonify({"error": "escolha Dots, PayPal ou Wise"}), 400
    if method == "wise":
        return _services.jsonify({"error": "selecione Wise ao solicitar saque; o vínculo é feito uma conta por vez"}), 400
    if method == "paypal" and (legal_name or destination_email):
        return _services.jsonify({"error": "PayPal usa o fluxo da Crowtado, sem destino manual. Atualize o QMoney."}), 400
    if method == "wise":
        if len(legal_name) < 2 or len(destination_email) > 254 or not _services.re.fullmatch(
                r"[^\s@]+@[^\s@]+\.[^\s@]+", destination_email):
            return _services.jsonify({"error": "informe nome legal e e-mail válido do destino"}), 400
    if _services.BALANCES_RUNNER.running or _services._withdraw_bulk_snapshot()["state"] == "running":
        return _services.jsonify({"error": "aguarde a operação de saldos terminar"}), 409
    configured = {a["email"] for a in _services._list_accounts()
                  if _services.org_policy.account_kind(str(a["email"])) == "crowtado"}
    creds = {e: p for e, p in _services._configured_crowtado_creds().items() if e in configured}
    if not creds:
        return _services.jsonify({"error": "não há contas Crowtado conectadas"}), 400
    with _services._PAYOUT_METHOD_LOCK:
        if _services._PAYOUT_METHOD_STATE["state"] == "running":
            return _services.jsonify({"error": "configuração em lote já está em andamento"}), 409
        _services._PAYOUT_METHOD_STATE.update(state="running", method=method, total=len(creds),
                                    done=0, results=[], current=None, message="")
    try:
        _services.threading.Thread(target=_services._payout_method_run,
                         args=(creds, method, legal_name, destination_email),
                         daemon=True, name="moneymin-payout-methods").start()
    except RuntimeError:
        with _services._PAYOUT_METHOD_LOCK:
            _services._PAYOUT_METHOD_STATE["state"] = "error"
        return _services.jsonify({"error": "não foi possível iniciar a configuração"}), 503
    return _services.jsonify({"ok": True, "total": len(creds)})
