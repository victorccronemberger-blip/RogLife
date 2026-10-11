"""Account-domain implementation; invoked with explicit dependency bindings."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('BALANCES_PATH', '_PERSISTENCE_LOCK', '_invalid_local_state', '_list_accounts', '_load_balances', '_on_claru_balance_result', '_removed_accounts', '_save_balances', '_validate_local_document', 'account_issue', 'crowtado', 'issue_text', 'load_json_state', 'math', 'org_policy', 'save_json', 'time', 'wallet')


def _balance_refresh_needed(_services, 
    accounts: list[dict[str, Any]], balances: dict[str, Any],
    connected: set[str], *, max_age_s: int = 24 * 3600,
) -> list[str]:
    """Contas conectadas sem leitura recente e confirmada de saldo."""
    now = _services.time.time()
    return sorted(str(account["email"]) for account in accounts
        if str(account.get("email") or "") in connected
        and _services.org_policy.account_kind(str(account["email"])) == "crowtado"
        and not _services.wallet.reading(balances.get(str(account["email"])), now=now,
                               max_age_s=max_age_s)["confirmed"])

def _confirmed_available_balance(_services, record: Any) -> bool:
    """Crowtado: only confirmed approved funds strictly above US$25 qualify."""
    if not isinstance(record, dict) or record.get("error") or record.get("stale"):
        return False
    if _services.crowtado.payout_in_transit(record):
        return False
    cents = record.get("availableCents")
    return (isinstance(cents, (int, float)) and not isinstance(cents, bool)
            and _services.math.isfinite(cents) and cents > 2500)

def _load_balances(_services) -> dict[str, Any]:
    """Cache de saldos: {email: {availableCents, ..., updated_at, error?}}."""
    value = _services.load_json_state(_services.BALANCES_PATH, {})
    if any(not isinstance(email, str) or not isinstance(record, dict) for email, record in value.items()):
        _services._invalid_local_state()
    return value

def _save_balances(_services, balances: dict[str, Any]) -> None:
    with _services._PERSISTENCE_LOCK:
        _services._validate_local_document(balances)
        if not isinstance(balances, dict) or any(not isinstance(email, str) or not isinstance(record, dict)
                                                for email, record in balances.items()):
            _services._invalid_local_state()
        _services._load_balances()
        _services.save_json(_services.BALANCES_PATH, balances)

def _invalidate_balance_after_withdrawal(_services, email: str, result: dict[str, Any]) -> None:
    """Keep the previous reading as historical, never infer the remaining balance."""
    with _services._PERSISTENCE_LOCK:
        balances = _services._load_balances()
        previous = balances.get(email)
        rec = dict(previous) if isinstance(previous, dict) else {}
        receipt = {
            "recorded_at": _services.time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "status": result.get("status"),
            "previousAvailableCents": rec.get("availableCents"),
            "previousUpdatedAt": rec.get("updated_at"),
        }
        amount = result.get("amountCents")
        if type(amount) is int and amount >= 0:
            receipt["amountCents"] = amount
        if result.get("currency") in {"USD", "BRL", "EUR", "GBP"}:
            receipt["currency"] = result["currency"]
        rec.update(stale=True, lastWithdrawal=receipt,
                   error="Saldo anterior à tentativa de saque. Atualize para consultar o valor atual.")
        if result.get("status") in {"on_hold", "hold"} and isinstance(result.get("holdReason"), str):
            rec["holdReason"] = result["holdReason"][:500]
            rec["error"] = "A Crowtado informou uma retenção de saque: " + rec["holdReason"]
        balances[email] = rec
        _services._save_balances(balances)

def _on_balance_result(_services, email: str, summary: dict | None, erro: str | Exception | None) -> None:
    if _services.org_policy.account_kind(email) == "claru":
        return _services._on_claru_balance_result(email, summary, erro)
    with _services._PERSISTENCE_LOCK:
        balances = _services._load_balances()
        previous = balances.get(email)
        rec = dict(previous) if isinstance(previous, dict) else {}
        if summary is not None:
            try:
                summary = _services.crowtado._summary_from_payload(summary)
                if not summary:
                    raise _services.crowtado.CrowtadoError("Saldo incompleto", code="invalid_response")
            except _services.crowtado.CrowtadoError as exc:
                summary, erro = None, exc
        rec["checked_at"] = _services.time.strftime("%Y-%m-%dT%H:%M:%S%z")
        if summary is not None:
            for field in _services.crowtado.BALANCE_SUMMARY_FIELDS:
                rec.pop(field, None)
            rec.update(summary)
            rec["updated_at"] = rec["checked_at"]
            rec["error"] = None
            rec["issue"] = None
            rec["stale"] = False
        else:
            error = erro if isinstance(erro, Exception) else RuntimeError(erro or "Consulta inconclusiva")
            issue = _services.account_issue(email, error, stage="Consulta de saldo Crowtado")
            rec["error"] = _services.issue_text(issue)
            rec["issue"] = issue
            rec["stale"] = True
        balances[email] = rec
        _services._save_balances(balances)

def _on_claru_balance_result(_services, email, summary, error):
    from moneymin import claru
    with _services._PERSISTENCE_LOCK:
        balances = _services._load_balances()
        previous = balances.get(email)
        record = dict(previous) if isinstance(previous, dict) and previous.get("provider") == "claru" else {}
        stamp = _services.time.strftime("%Y-%m-%dT%H:%M:%S%z")
        try:
            if error is not None:
                raise error if isinstance(error, Exception) else claru.ClaruError("Consulta de saldo Claru inconclusiva.")
            checked = claru.validate_balance(summary)
            record = {**checked, "checked_at": stamp, "updated_at": stamp, "stale": False, "error": None, "issue": None}
        except Exception as exc:
            record.update(provider="claru", checked_at=stamp, stale=True,
                          error=str(exc) if isinstance(exc, claru.ClaruError) else "Consulta de saldo Claru inconclusiva. Confira a conexão e tente novamente.",
                          issue={"provider": "claru", "code": "unknown"})
        balances[email] = record
        _services._save_balances(balances)

def _balance_accounts(_services):
    from moneymin import claru
    accounts = {r["email"]: r for r in _services._list_accounts()}
    removed = _services._removed_accounts()
    connections = {r["minute_email"]: r for r in claru.public_accounts() if r["minute_email"] not in removed}
    for email, row in connections.items():
        accounts.setdefault(email, {"email": email})
    return list(accounts.values()), connections
