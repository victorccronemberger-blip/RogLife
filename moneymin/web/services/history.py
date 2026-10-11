"""history services: existing behavior with explicit dependencies."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('_public_event', 'campaign', 'config', 'friendly_campaign_error', 'load_json_state', 'math')


def _read_campaign_log(_services, path: Path) -> dict[str, Any]:
    """Um arquivo danificado não pode derrubar a lista inteira do histórico."""
    data = _services.load_json_state(path, None)
    if not isinstance(data, dict):
        raise ValueError("log inválido")
    for key in ("items", "accounts", "issues"):
        if not isinstance(data.get(key, []), list):
            raise ValueError("log inválido")
    for item in data.get("items", []):
        if (not isinstance(item, dict) or not isinstance(item.get("accounts", []), list)
                or any(not isinstance(account, dict) for account in item.get("accounts", []))):
            raise ValueError("log inválido")
        for account in item.get("accounts", []):
            if any(type(account[key]) is not bool for key in ("ok", "finalized", "skipped", "recovered")
                   if key in account and not (key == "finalized" and account[key] is None)):
                raise ValueError("confirmação inválida no histórico")
        try:
            duration = float(item.get("duration_ms") or 0)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("duração inválida no histórico") from exc
        if not _services.math.isfinite(duration) or duration < 0:
            raise ValueError("duração inválida no histórico")
    return data

def _campaign_log_view(_services, data: dict[str, Any], *, history_name: str | None = None) -> dict[str, Any]:
    """Resumo e evidências permitidas; nunca inclui respostas brutas ou segredos."""
    configured = [str(email) for email in data.get("accounts", []) if email]
    by_account: dict[str, dict[str, Any]] = {
        email: {"email": email, "success": 0, "failed": 0, "skipped": 0, "pending": 0}
        for email in configured
    }
    items: list[dict[str, Any]] = []
    from ...campaign_evidence import current_groups, current_result
    groups = current_groups() if history_name is not None else {}
    current_summary = dict.fromkeys(("confirmed", "pending", "review", "unknown"), 0)
    delivery_summary = dict(current_summary)
    total_success = total_failed = total_skipped = 0
    total_pending = 0
    for index, raw_item in enumerate(data.get("items", []), 1):
        if not isinstance(raw_item, dict):
            continue
        results: list[dict[str, Any]] = []
        for raw_result in raw_item.get("accounts", []):
            if not isinstance(raw_result, dict):
                continue
            email = str(raw_result.get("email") or "Conta")
            stats = by_account.setdefault(
                email, {"email": email, "success": 0, "failed": 0, "skipped": 0, "pending": 0}
            )
            if raw_result.get("skipped"):
                status = "skipped"
                detail = (_services.friendly_campaign_error(raw_result.get("error"))
                          if raw_result.get("error") else
                          "Registro local indica envio anterior." if raw_result.get("reason") == "already_sent" else
                          "Conta pulada nesta execução; o histórico não informa o motivo.")
                stats["skipped"] += 1
                total_skipped += 1
            elif raw_result.get("ok") and raw_result.get("finalized") is not True:
                status = "pending"
                detail = ("Envio sem confirmação de finalização. Confira a sessão antes de reenviar."
                          if raw_result.get("finalized") is False else
                          "Registro antigo sem evidência explícita de finalização. Confira a sessão antes de reenviar.")
                stats["pending"] += 1
                total_pending += 1
            elif raw_result.get("ok"):
                status = "success"
                detail = "Finalização confirmada pelo serviço e registrada nesta execução."
                stats["success"] += 1
                total_success += 1
            else:
                status = "failed"
                detail = _services.friendly_campaign_error(raw_result.get("error"))
                stats["failed"] += 1
                total_failed += 1
            identifier = raw_result.get("session_id")
            current = current_result(raw_item, raw_result, history_name, groups)
            if status != "skipped":
                current_summary[current["status"]] += 1
                delivery_summary[current["status"]] += 1
            results.append({"email": email, "status": status, "detail": detail,
                            "session_id": identifier if isinstance(identifier, str) else None,
                            "confirmation": ("remote_ack" if status == "success" and raw_result.get("finalized") is True
                                             else "legacy_record" if status == "pending" and raw_result.get("finalized") is None
                                             else "not_confirmed"),
                            "recovered": raw_result.get("recovered") is True,
                            "current_result": current})
        task = str(raw_item.get("task_name") or raw_item.get("task_scenario")
                   or raw_item.get("scenario") or f"Vídeo {index}")
        duration_s = max(0, int(float(raw_item.get("duration_ms") or 0) / 1000))
        items.append({
            "index": index,
            "clip_uid": raw_item.get("clip_uid") if isinstance(raw_item.get("clip_uid"), str) else None,
            "task": task,
            "duration_s": duration_s,
            "success": sum(result["status"] == "success" for result in results),
            "failed": sum(result["status"] == "failed" for result in results),
            "skipped": sum(result["status"] == "skipped" for result in results),
            "pending": sum(result["status"] == "pending" for result in results),
            "accounts": results,
        })
    return {
        "schema": 2,
        "started_at": data.get("started_at"),
        "summary": {
            "configured_accounts": len(configured),
            "videos": len(items),
            "success": total_success,
            "failed": total_failed,
            "skipped": total_skipped,
            "pending": total_pending,
        },
        "accounts": sorted(by_account.values(), key=lambda item: item["email"].lower()),
        "items": items,
        "status": data.get("status"),
        "current_summary": current_summary,
        "delivery_summary": delivery_summary,
        "issues": [event for issue in data.get("issues", [])
                   if isinstance(issue, dict)
                   and (event := _services._public_event(str(issue.get("kind") or ""), issue))],
    }

def _log_path(_services, name: str) -> Path | None:
    """Resolve um nome de log de forma segura (sem path traversal)."""
    if not _services.campaign.is_campaign_history_name(name) or "/" in name \
            or "\\" in name or name == "campaign.example.json":
        return None
    path = _services.config.DATA_DIR / name
    return path if path.exists() else None
