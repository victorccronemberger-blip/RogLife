"""Account-domain implementation; invoked with explicit dependency bindings."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('_PAYOUT_METHOD_LOCK', '_PAYOUT_METHOD_STATE', '_PAYOUT_OPERATION_LOCK', '_WALLET_COMMAND_LOCK', '_wise_cleanup_snapshot', 'account_issue', 'crowtado', 'jsonify', 'wraps')


def _serialize_wallet_command(_services, handler):
    """Admit one command at a time before publishing its worker state."""
    @_services.wraps(handler)
    def command(*args, **kwargs):
        if not _services._WALLET_COMMAND_LOCK.acquire(blocking=False):
            return _services.jsonify({"error": "aguarde a operação atual da Carteira terminar"}), 409
        try:
            return handler(*args, **kwargs)
        finally:
            _services._WALLET_COMMAND_LOCK.release()
    return command

def _payout_method_snapshot(_services) -> dict[str, Any]:
    with _services._PAYOUT_METHOD_LOCK:
        return {**_services._PAYOUT_METHOD_STATE, "results": list(_services._PAYOUT_METHOD_STATE["results"])}

def _payout_method_run(_services, creds: dict[str, str], method: str,
                       legal_name: str, destination_email: str) -> None:
    blocked = False
    for email, password in creds.items():
        with _services._PAYOUT_METHOD_LOCK:
            _services._PAYOUT_METHOD_STATE["current"] = email
        try:
            with _services._PAYOUT_OPERATION_LOCK:
                if _services._wise_cleanup_snapshot().get("pending"):
                    raise ValueError("Conclua a limpeza Wise pendente antes de configurar métodos.")
                _services.crowtado.configurar_metodo_saque(email, password, method,
                                                 legal_name, destination_email)
            result = {"email": email, "ok": True, "message": "configurado"}
        except Exception as exc:
            # A resposta pode repetir senha, token ou URL assinada. Só a
            # classificação conhecida entra no estado consultado pela UI.
            issue = _services.account_issue(email, exc, stage="Configuração de saque Crowtado")
            result = {"email": email, "ok": False, "code": issue["code"],
                      "message": issue["reason"] + " " + issue["action"]}
            if method == "wise" and (getattr(exc, "account_issue_code", None) == "destination_in_use"
                                     or "already linked to anoth" in str(exc).casefold()):
                blocked = True
                result.update(code="destination_in_use",
                    message="A Crowtado informou que este destino Wise já está vinculado a outra conta.")
        with _services._PAYOUT_METHOD_LOCK:
            _services._PAYOUT_METHOD_STATE["results"].append(result)
            _services._PAYOUT_METHOD_STATE["done"] += 1
            _services._PAYOUT_METHOD_STATE["current"] = None
        if blocked:
            break
    with _services._PAYOUT_METHOD_LOCK:
        _services._PAYOUT_METHOD_STATE["state"] = "blocked" if blocked else "done"
        if blocked:
            _services._PAYOUT_METHOD_STATE["message"] = (
                "A Crowtado não permite vincular este mesmo destino Wise a outra conta. "
                "As contas restantes não foram alteradas.")
