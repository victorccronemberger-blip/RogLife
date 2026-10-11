"""Remote receipt proofs and terminal reconciliation rules."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .acceptance import STATE_FAILED, STATE_RETRY_LATE, STATE_TRANSPORT, STATE_COMPLETING


@dataclass(frozen=True)
class ReceiptServices:
    upload_error: type[Exception]
    sidecar_filename: Callable[..., str]
    response_upload_id: Callable[..., str]
    journal_recorded_at: Callable[..., str]
    journal_flags_valid: Callable[..., bool]
    recorded_at_to_wall_ms: Callable[..., int | None]
    format_recorded_at: Callable[..., str]
    iso_now: Callable[[], str]
    is_pending_transport: Callable[[Any], bool]
    remote_recovery_identity: Callable[..., dict[str, Any]]
    session_resource_identity: Callable[..., tuple[str, str]]
    validate_remote_recovery_receipt: Callable[..., str]
    terminal_failure_marker: Callable[..., dict[str, Any]]
    is_terminal_remote_failure: Callable[..., bool]


def _remote_recovery_identity(row: dict[str, Any], services: ReceiptServices) -> dict[str, Any]:
    """Return strict journal identity fields used by remote receipt proofs."""
    try:
        sid, index = row.get("session_id"), row.get("chunk_index", 0)
        expected_chunks = row.get("expected_chunk_count", 1)
        services.sidecar_filename(sid, index)
        upload_id = services.response_upload_id({"id": row.get("upload_id")})
        recorded_at = services.journal_recorded_at(row)
        duration_ms = row.get("duration_ms")
        email, org_key = row.get("account_email"), row.get("org_key")
        task_id = row.get("task_id")
        if (not services.journal_flags_valid(row)
                or row.get("native_response_schema") is not True
                or row.get("create_attempted") is not True
                or row.get("register_first") is not True
                or type(index) is not int or index < 0
                or type(expected_chunks) is not int or expected_chunks < 1 or index >= expected_chunks
                or type(duration_ms) is not int or duration_ms <= 0
                or not isinstance(email, str) or not email.strip()
                or not isinstance(org_key, str) or not org_key.strip()
                or (task_id is not None and (not isinstance(task_id, str) or not task_id.strip()))
                or row.get("log_id") != f"{sid}_{index}"
                or row.get("filename") != f"{sid}_{index}.mp4"):
            raise ValueError
        return {
            "upload_id": upload_id,
            "session_id": sid,
            "log_id": row["log_id"],
            "recorded_at": recorded_at,
            "duration_ms": duration_ms,
            "org_key": org_key,
            "email": email.strip().casefold(),
            "task_id": task_id,
        }
    except (KeyError, TypeError, ValueError, services.upload_error):
        raise services.upload_error("Identidade remota do recibo inválida; preservado para revisão.",
                          transient=False, phase="query", review_required=True) from None


def _session_resource_identity(session: Any, services: ReceiptServices, owner_resource_key: str | None = None) -> tuple[str, str]:
    email = getattr(session, "email", None)
    if not isinstance(email, str) or not email.strip():
        raise services.upload_error("Sessão sem identidade confirmada para consultar recibo.",
                          transient=False, phase="query", review_required=True)
    email = email.strip().casefold()
    if owner_resource_key is None:
        me = getattr(session, "me", None)
        if not callable(me):
            raise services.upload_error("Identidade de usuário Minute indisponível para consultar recibo.",
                              transient=False, phase="query", review_required=True)
        try:
            me = me()
        except Exception:
            raise services.upload_error("Identidade de usuário Minute não pôde ser confirmada.",
                              transient=True, phase="query") from None
        if (not isinstance(me, dict) or not isinstance(me.get("email"), str)
                or me["email"].strip().casefold() != email):
            raise services.upload_error("Identidade de usuário Minute divergente; recibo preservado.",
                              transient=False, phase="query", review_required=True)
        owner_resource_key = me.get("resourceKey")
    if not isinstance(owner_resource_key, str) or not owner_resource_key.strip():
        raise services.upload_error("Chave de usuário Minute ausente; recibo preservado para revisão.",
                          transient=False, phase="query", review_required=True)
    return email, owner_resource_key.strip()


def validate_remote_recovery_receipt(
    row: dict[str, Any], receipt: Any, session: Any, services: ReceiptServices, *,
    owner_resource_key: str | None = None,
) -> str:
    """Strictly bind an authenticated GET /uploads detail to its local row.

    The returned state is intentionally limited to the API's documented
    ``initiated``, ``uploaded``, ``completed`` and ``failed`` values. Unknown
    states never authorize a blob transfer or completion request.
    """
    identity = services.remote_recovery_identity(row)
    session_email, resource_key = services.session_resource_identity(session, owner_resource_key)
    expected_id = {
        "uploadId": identity["upload_id"],
        "sessionId": identity["session_id"],
        "logId": identity["log_id"],
        "orgResourceKey": identity["org_key"],
        "userResourceKey": resource_key,
    }
    required = set(expected_id) | {
        "userEmail", "taskId", "taskName", "recordedAt", "createdAt", "durationMs",
        "status", "orgName", "storageAccount",
    }
    if (not isinstance(receipt, dict) or not required.issubset(receipt)
            or any(receipt.get(key) != value for key, value in expected_id.items())
            or not isinstance(receipt.get("userEmail"), str)
            or not isinstance(receipt.get("userResourceKey"), str)
            or any(receipt.get(key) is not None and not isinstance(receipt.get(key), str)
                   for key in ("taskId", "taskName", "orgName", "storageAccount"))
            or not isinstance(receipt.get("recordedAt"), str)
            or not isinstance(receipt.get("createdAt"), str)
            or services.recorded_at_to_wall_ms(receipt["createdAt"]) is None):
        raise services.upload_error("Resposta remota não corresponde ao recibo local; preservado para revisão.",
                          transient=False, phase="query", review_required=True)
    receipt_email = receipt.get("userEmail")
    if (not isinstance(receipt_email, str)
            or receipt_email.strip().casefold() != identity["email"]
            or receipt_email.strip().casefold() != session_email):
        raise services.upload_error("Dono do recibo remoto diverge da sessão; preservado para revisão.",
                          transient=False, phase="query", review_required=True)
    receipt_task = receipt.get("taskId")
    if (receipt_task is not None and not isinstance(receipt_task, str)) or receipt_task != identity["task_id"]:
        raise services.upload_error("Tarefa do recibo remoto diverge do registro local; preservado para revisão.",
                          transient=False, phase="query", review_required=True)
    remote_recorded_at = receipt.get("recordedAt")
    if (not isinstance(remote_recorded_at, str)
            or services.recorded_at_to_wall_ms(remote_recorded_at)
            != services.recorded_at_to_wall_ms(identity["recorded_at"])):
        raise services.upload_error("Horário do recibo remoto diverge do registro local; preservado para revisão.",
                          transient=False, phase="query", review_required=True)
    duration_ms = receipt.get("durationMs")
    if type(duration_ms) is not int or duration_ms != identity["duration_ms"]:
        raise services.upload_error("Duração do recibo remoto diverge do registro local; preservado para revisão.",
                          transient=False, phase="query", review_required=True)
    status = receipt.get("status")
    if not isinstance(status, str) or status not in {"initiated", "uploaded", "completed", "failed"}:
        raise services.upload_error("Estado remoto do recibo não reconhecido; preservado para revisão.",
                          transient=False, phase="query", review_required=True)
    if status == "completed" and not isinstance(receipt.get("meta"), dict):
        raise services.upload_error("Recibo concluído sem metadados válidos; preservado para revisão.",
                          transient=False, phase="query", review_required=True)
    return status




__all__ = ["ReceiptServices", "_remote_recovery_identity", "_session_resource_identity", "validate_remote_recovery_receipt"]
