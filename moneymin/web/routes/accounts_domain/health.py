"""Route handlers grouped by account workflow."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('RECOVERY', 'RUNNER', 'ThreadPoolExecutor', '_archive_checked_ban', '_ban_accounts', '_check_account_health', '_list_accounts', 'account_health', 'account_issue', 'as_completed', 'issue_text', 'jsonify')


def check_account(_services, email: str):
    result = _services._check_account_health(email)
    _services._archive_checked_ban(email, result)
    ok = result["status"] == "active"
    return _services.jsonify({"ok": ok, **result}), 200 if ok else 400

def check_all_accounts(_services):
    if _services.RUNNER.running or _services.RECOVERY.running:
        return _services.jsonify({
            "error": "aguarde ou pare a campanha antes de verificar todas as contas",
        }), 409
    emails = [account["email"] for account in _services._list_accounts()]
    results_by_email: dict[str, dict[str, Any]] = {}
    if emails:
        workers = min(3, len(emails))
        with _services.ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="moneymin-account-check") as pool:
            futures = {pool.submit(_services._check_account_health, email): email
                       for email in emails}
            for future in _services.as_completed(futures):
                email = futures[future]
                try:
                    results_by_email[email] = future.result()
                except Exception as exc:  # noqa: BLE001 — uma conta não mata o lote
                    issue = _services.account_issue(email, exc)
                    results_by_email[email] = {
                        "email": email, "status": "inconclusive", "status_label": "Verificação inconclusiva",
                        "error": _services.issue_text(issue), "issue": issue,
                    }
    results = [results_by_email[email] for email in emails]
    confirmed = [(result, issue) for result in results
                 if (issue := _services.account_health.confirmed_ban(result["email"], result))]
    if confirmed:
        _services._ban_accounts([issue for _, issue in confirmed])
        for result, _ in confirmed:
            result["permanently_removed"] = True
    return _services.jsonify({
        "ok": True,
        "total": len(results),
        "active": sum(result["status"] == "active" for result in results),
        "disabled": [result for result in results
                     if result["status"] == "disabled"],
        "errors": [result for result in results if result["status"] not in ("active", "disabled")],
        "inconclusive": sum(result["status"] == "inconclusive" for result in results),
        "results": results,
    })
