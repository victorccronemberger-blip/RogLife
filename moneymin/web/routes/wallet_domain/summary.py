"""Route handlers grouped by account workflow."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('BALANCES_RUNNER', '_balance_accounts', '_configured_crowtado_creds', '_crowtado_creds', '_last_withdrawal_receipt', '_load_balances', '_payout_method_snapshot', '_saved_account_password', '_wise_cleanup_snapshot', '_withdraw_bulk_snapshot', 'fx', 'jsonify', 'org_policy', 'wallet')


def get_balances(_services):
    account_rows, claru_connections = _services._balance_accounts()
    balances = _services._load_balances()
    configured = sorted(a["email"] for a in account_rows)
    configured_set = set(configured)
    saved_passwords = _services._crowtado_creds()
    # O cofre pode conservar credenciais de identidades removidas. Elas não
    # pertencem mais à operação atual e não devem inflar a contagem exibida
    # nem aparecer como contas conectadas no desktop.
    with_password = sorted(
        email for email in (set(_services._configured_crowtado_creds()) | set(claru_connections)) if email in configured_set
    )
    kinds = {str(account["email"]): _services.org_policy.account_kind(str(account["email"]))
             for account in account_rows}
    runner_state = _services.BALANCES_RUNNER.snapshot()
    wallet_state = _services.wallet.snapshot(account_rows, balances, set(with_password), kinds, runner_state)
    return _services.jsonify({
        "balances": {email: record for email, record in balances.items() if email in configured_set},
        "accounts": configured,
        "account_kinds": {
            str(account["email"]): _services.org_policy.account_kind(str(account["email"]))
            for account in account_rows
        },
        "claru_connections": claru_connections,
        "with_password": with_password,
        "with_saved_password": sorted(
            account["email"] for account in account_rows
            if _services._saved_account_password(account["email"], saved_passwords)
        ),
        "refresh_needed": sorted(email for email, row in wallet_state["accounts"].items() if row["refresh_needed"]),
        "runner": runner_state,
        "wallet": wallet_state,
        "withdraw_bulk": _services._withdraw_bulk_snapshot(),
        "payout_method_bulk": _services._payout_method_snapshot(),
        "wise_cleanup": _services._wise_cleanup_snapshot(),
        "last_withdrawal": _services._last_withdrawal_receipt(configured_set),
        "exchange": _services.fx.usd_brl_quote(),
    })
