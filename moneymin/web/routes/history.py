"""history routes: existing behavior with explicit dependencies."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('AuthError', 'RECOVERY', 'RUNNER', 'Session', 'ThreadPoolExecutor', '_HEAVY_RUNNER_LOCK', '_campaign_log_view', '_identity_route', '_log_path', '_read_campaign_log', '_safe_org', 'account_issue', 'campaign', 'json', 'jsonify', 'request', 'sent_registry')


def get_logs(_services):
    out: list[dict[str, Any]] = []
    for path in _services.campaign.list_campaign_logs():
        try:
            data = _services._read_campaign_log(path)
        except (OSError, ValueError):
            continue
        items = data.get("items", [])
        sends = [acc for it in items for acc in it.get("accounts", [])
                 if not acc.get("skipped")]
        out.append({
            "name": path.name,
            "started_at": data.get("started_at"),
            "accounts": data.get("accounts", []),
            "items": len(items),
            "sends": len(sends),
            "ok": sum(1 for s in sends if s.get("ok") is True and s.get("finalized") is True),
        })
    return _services.jsonify({"logs": out})

def get_log(_services, name: str):
    path = _services._log_path(name)
    if path is None:
        return _services.jsonify({"error": "log não encontrado"}), 404
    try:
        raw = _services._read_campaign_log(path)
        return _services.jsonify(_services._campaign_log_view(raw, history_name=path.name))
    except (OSError, ValueError):
        return _services.jsonify({"error": "log ilegível (JSON vazio/corrompido)"}), 400

def get_log_status(_services, name: str):
    path = _services._log_path(name)
    if path is None:
        return _services.jsonify({"error": "log não encontrado"}), 404
    try:
        data = _services._read_campaign_log(path)
    except (OSError, ValueError):
        return _services.jsonify({"error": "log ilegível (JSON vazio/corrompido)"}), 400
    entries: list[tuple[str, str, str, int]] = []
    for it in data.get("items", []):
        for acc in it.get("accounts", []):
            sid = acc.get("session_id")
            if not sid:
                continue
            uploads = acc.get("uploads")
            expected_files = len(uploads) if isinstance(uploads, list) else 1
            expected_files = max(1, expected_files)
            org = acc.get("org_key") or _services._safe_org(acc.get("email", ""))
            if not org:
                entries.append((str(acc.get("email") or ""), "", str(sid),
                                expected_files))
                continue
            entries.append((str(acc.get("email") or ""), str(org), str(sid),
                            expected_files))

    # Uma sessão autenticada por conta evita renovar o mesmo refresh token
    # para cada vídeo. Contas diferentes são consultadas em paralelo; as
    # sessões da mesma conta continuam seriais para não disputar tokens.
    grouped: dict[str, list[tuple[str, str, int]]] = {}
    for email, org, sid, expected_files in entries:
        grouped.setdefault(email, []).append((org, sid, expected_files))

    def _check_account(
        group: tuple[str, list[tuple[str, str, int]]],
    ) -> list[dict[str, Any]]:
        email, account_entries = group
        output: list[dict[str, Any]] = []
        valid = [(org, sid, expected) for org, sid, expected in account_entries
                 if org]
        invalid = [(org, sid, expected) for org, sid, expected in account_entries
                   if not org]
        output.extend({"session_id": sid, "email": email,
                       "status": "sem org_key", "expected_files": expected}
                      for _, sid, expected in invalid)
        if not valid:
            return output
        processed: set[tuple[str, str]] = set()
        try:
            with _services._identity_route(email):
                sess = _services.Session.from_email(email)
                for org, sid, expected in valid:
                    try:
                        result = _services.campaign.session_result(
                            email, org, sid, session=sess)
                        result["expected_files"] = expected
                        output.append(result)
                        processed.add((org, sid))
                    except (_services.AuthError, RuntimeError, OSError, _services.json.JSONDecodeError) as exc:
                        issue = _services.account_issue(email, exc, stage="Consulta de sessões Minute")
                        output.append({"session_id": sid, "email": email,
                                       "status": "erro: consulta indisponível", "code": "session_status_unavailable",
                                       "issue": issue,
                                       "expected_files": expected})
                        processed.add((org, sid))
        except (_services.AuthError, RuntimeError, OSError, ValueError) as exc:
            issue = _services.account_issue(email, exc, stage="Consulta de sessões Minute")
            return output + [
                {"session_id": sid, "email": email,
                 "status": "erro: acesso indisponível", "code": "session_access_unavailable",
                 "issue": issue, "expected_files": expected}
                for org, sid, expected in valid if (org, sid) not in processed]
        return output

    results: list[dict[str, Any]] = []
    groups = list(grouped.items())
    if groups:
        workers = min(6, len(groups))
        with _services.ThreadPoolExecutor(max_workers=workers) as pool:
            for account_results in pool.map(_check_account, groups):
                results.extend(account_results)
    session_ready = sum(item.get("status") == "preview_ready" for item in results)
    session_pending = sum(item.get("status") == "processing" for item in results)
    session_unavailable = sum(
        str(item.get("status") or "").startswith("unprocessed:unavailable")
        for item in results)
    session_errors = (len(results) - session_ready - session_pending
                      - session_unavailable)

    # A interface fala em prévias, portanto o progresso precisa contar os
    # arquivos/chunks reais, não apenas as sessões. Uma sessão longa pode
    # conter vários MP4s e pode estar parcialmente processada.
    ready = pending = unavailable = total = errors = transient_errors = 0
    for item in results:
        expected = max(1, int(item.get("expected_files") or 1))
        reported = max(0, int(item.get("total_files") or 0))
        status = str(item.get("status") or "")
        if status.startswith("erro:"):
            total += expected
            errors += expected
            transient_errors += expected
        elif status == "sem org_key":
            total += expected
            errors += expected
        else:
            item_total = max(expected, reported)
            item_ready = max(0, int(item.get("ready_files") or 0))
            item_pending = max(0, int(item.get("pending_files") or 0))
            item_unavailable = max(
                0, int(item.get("unavailable_files") or 0))
            missing = max(
                0, item_total - item_ready - item_pending - item_unavailable)
            total += item_total
            ready += item_ready
            pending += item_pending
            unavailable += item_unavailable
            if status == "processing":
                pending += missing
            else:
                errors += missing
    return _services.jsonify({
        "results": results,
        "attempt_status": data.get("status"),
        "delivery_summary": (delivery := _services._campaign_log_view(data, history_name=path.name)["delivery_summary"]),
        "all_deliveries_confirmed": bool(sum(delivery.values())) and delivery["confirmed"] == sum(delivery.values()),
        "summary": {
            "total": total, "ready": ready, "pending": pending,
            "unavailable": unavailable, "errors": errors,
            "transient_errors": transient_errors,
        },
        "sessions": {
            "total": len(results), "ready": session_ready,
            "pending": session_pending, "unavailable": session_unavailable,
            "errors": session_errors,
        },
    })

def get_sent(_services):
    try:
        return _services.jsonify({"sent": _services.sent_registry.summary()})
    except ValueError as exc:
        return _services.jsonify({"error": str(exc)}), 409

def reset_sent(_services):
    with _services._HEAVY_RUNNER_LOCK:
        if _services.RUNNER.running or _services.RECOVERY.running:
            return _services.jsonify({
                "error": "aguarde a campanha terminar antes de resetar a lista de vídeos usados",
            }), 409
        body = _services.request.get_json(silent=True) or {}
        scenario = body.get("scenario")
        try:
            _services.sent_registry.reset(str(scenario) if scenario else None)
        except ValueError as exc:
            return _services.jsonify({"error": str(exc)}), 409
        return _services.jsonify({"ok": True, "sent": _services.sent_registry.summary()})
