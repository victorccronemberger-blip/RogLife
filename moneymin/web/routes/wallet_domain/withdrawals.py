"""Route handlers grouped by account workflow."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('BALANCES_RUNNER', '_configured_crowtado_creds', '_confirmed_available_balance', '_list_accounts', '_load_balances', '_payout_method_snapshot', '_start_withdraw_worker', '_wise_cleanup_snapshot', '_wise_withdraw_options', '_withdraw_bulk_snapshot', '_withdraw_once', 'crowtado', 'jsonify', 'org_policy', 'request', 'wallet')


def request_balance_withdraw(_services):
    """Solicita o método salvo ou o fluxo Wise confirmado pelo usuário."""
    body = _services.request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return _services.jsonify({"error": "requisição inválida"}), 400
    try:
        wise = _services._wise_withdraw_options(body)
    except ValueError as exc:
        return _services.jsonify({"error": str(exc)}), 400
    if _services._payout_method_snapshot()["state"] == "running":
        return _services.jsonify({"error": "aguarde a configuração dos métodos terminar"}), 409
    email = str(body.get("email", "")).strip()
    if not email:
        return _services.jsonify({"error": "informe a conta"}), 400
    configured = {a["email"] for a in _services._list_accounts()}
    if email not in configured:
        return _services.jsonify({"error": "conta não está configurada"}), 404
    if _services.org_policy.account_kind(email) == "claru":
        return _services.jsonify({"error": "saldo Crowtado não se aplica a contas Claru"}), 400
    password = _services._configured_crowtado_creds().get(email)
    if not password:
        return _services.jsonify({"error": "salve a senha do crowtado primeiro"}), 400
    if _services.BALANCES_RUNNER.running:
        return _services.jsonify({"error": "aguarde a consulta de saldos terminar"}), 409
    if _services._withdraw_bulk_snapshot()["state"] == "running":
        return _services.jsonify({"error": "aguarde o saque em lote terminar"}), 409
    balance = _services._load_balances().get(email)
    if isinstance(balance, dict) and _services.crowtado.payout_in_transit(balance):
        return _services.jsonify({"error": "há um pagamento em trânsito que a Crowtado não permite substituir; atualize o saldo após a conclusão"}), 409
    if not _services._confirmed_available_balance(balance):
        return _services.jsonify({"error": "atualize o saldo desta conta antes de solicitar saque; é necessário saldo aprovado superior a US$ 25,00"}), 400
    eligibility = _services.wallet.payout(balance, _services.wallet.reading(balance), True)
    if not eligibility["eligible"]:
        return _services.jsonify({"error": eligibility["reason"], "code": eligibility["code"]}), 409
    if wise and wise.get("method") != "paypal" and body.get("background") is True:
        if _services._wise_cleanup_snapshot().get("pending"):
            return _services.jsonify({"error": "há uma limpeza Wise pendente; nenhum novo saque será enviado"}), 409
        payload, status = _services._start_withdraw_worker({email: password}, wise)
        return _services.jsonify(payload), status
    result, status = (_services._withdraw_once(email, password, wise) if wise
                      else _services._withdraw_once(email, password))
    return _services.jsonify(result), status

def request_all_balance_withdrawals(_services):
    """Processa contas elegíveis sequencialmente, incluindo a limpeza Wise."""
    if _services._wise_cleanup_snapshot().get("pending"):
        return _services.jsonify({"error": "conclua a limpeza Wise pendente antes de iniciar outro lote"}), 409
    body = _services.request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return _services.jsonify({"error": "requisição inválida"}), 400
    try:
        wise = _services._wise_withdraw_options(body)
    except ValueError as exc:
        return _services.jsonify({"error": str(exc)}), 400
    if _services._payout_method_snapshot()["state"] == "running":
        return _services.jsonify({"error": "aguarde a configuração dos métodos terminar"}), 409
    if _services.BALANCES_RUNNER.running:
        return _services.jsonify({"error": "aguarde a consulta de saldos terminar"}), 409
    configured = {a["email"] for a in _services._list_accounts()}
    passwords = _services._configured_crowtado_creds()
    balances = _services._load_balances()
    eligible = {}
    for email in sorted(configured):
        if _services.org_policy.account_kind(email) != "crowtado" or email not in passwords:
            continue
        balance = balances.get(email) or {}
        if not _services.wallet.payout(balance, _services.wallet.reading(balance), True)["eligible"]:
            continue
        eligible[email] = passwords[email]
    if not eligible:
        return _services.jsonify({"error": "não há contas elegíveis: é necessário saldo aprovado superior a US$ 25,00 e nenhum pagamento em trânsito bloqueando novo saque"}), 400
    payload, status = _services._start_withdraw_worker(eligible, wise)
    return _services.jsonify(payload), (200 if status == 202 else status)
