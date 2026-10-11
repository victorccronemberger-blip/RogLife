"""Reconcile remote terminal receipts with locally durable journal groups."""
from __future__ import annotations

from typing import Any

from .acceptance import STATE_FAILED, STATE_RETRY_LATE, STATE_TRANSPORT, STATE_COMPLETING
from .receipts import ReceiptServices


def _terminal_failure_marker(row: dict[str, Any], services: ReceiptServices) -> dict[str, Any]:
    identity = services.remote_recovery_identity(row)
    marker = row.get("remote_terminal_failure")
    if not isinstance(marker, dict):
        raise ValueError("remote terminal failure proof mismatch")
    expected = {
        "upload_id": identity["upload_id"],
        "session_id": identity["session_id"],
        "log_id": identity["log_id"],
        "email": identity["email"],
        "user_resource_key": marker.get("user_resource_key"),
        "org_key": identity["org_key"],
        "task_id": identity["task_id"],
        "recorded_at": identity["recorded_at"],
        "duration_ms": identity["duration_ms"],
        "chunk_index": row.get("chunk_index", 0),
        "expected_chunk_count": row.get("expected_chunk_count", 1),
    }
    if (type(marker.get("version")) is not int or marker.get("version") != 1
            or marker.get("status") != "failed"
            or row.get("finalized") is True or row.get("campaign_reconciled") is True
            or type(marker.get("duration_ms")) is not int
            or type(marker.get("chunk_index")) is not int
            or type(marker.get("expected_chunk_count")) is not int
            or not isinstance(marker.get("user_resource_key"), str)
            or not marker["user_resource_key"].strip()
            or any(marker.get(key) != value for key, value in expected.items())):
        raise ValueError("remote terminal failure proof mismatch")
    checked_at = marker.get("checked_at")
    checked_ms = services.recorded_at_to_wall_ms(checked_at) if isinstance(checked_at, str) else None
    if (checked_ms is None or services.format_recorded_at(checked_ms / 1000.0) != checked_at):
        raise ValueError("remote terminal failure timestamp invalid")
    return marker


def is_terminal_remote_failure(row: Any, services: ReceiptServices) -> bool:
    if not isinstance(row, dict) or row.get("state") != STATE_FAILED \
            or row.get("phase") != "remote_terminal_failure":
        return False
    try:
        services.terminal_failure_marker(row)
    except (TypeError, ValueError, services.upload_error):
        return False
    return True


def mark_remote_terminal_failure(
    row: dict[str, Any], receipt: Any, session: Any, services: ReceiptServices, *,
    checked_at: str | None = None, owner_resource_key: str | None = None,
) -> dict[str, Any]:
    _session_email, resource_key = services.session_resource_identity(session, owner_resource_key)
    status = services.validate_remote_recovery_receipt(
        row, receipt, session, owner_resource_key=resource_key)
    if status != "failed":
        raise services.upload_error("O recibo remoto não confirmou falha terminal.",
                          transient=False, phase="query", review_required=True)
    if (not isinstance(row.get("state"), str)
            or row.get("state") not in {STATE_FAILED, STATE_RETRY_LATE, STATE_TRANSPORT, STATE_COMPLETING}
            or row.get("finalized") is True or row.get("campaign_reconciled") is True):
        raise services.upload_error("Falha remota não pode substituir um estado local confirmado.",
                          transient=False, phase="query", review_required=True)
    identity = services.remote_recovery_identity(row)
    checked_at = checked_at or services.iso_now()
    checked_ms = services.recorded_at_to_wall_ms(checked_at) if isinstance(checked_at, str) else None
    if checked_ms is None or services.format_recorded_at(checked_ms / 1000.0) != checked_at:
        raise services.upload_error("Horário da confirmação terminal inválido.",
                          transient=False, phase="query", review_required=True)
    row["remote_terminal_failure"] = {
        "version": 1,
        "status": "failed",
        "upload_id": identity["upload_id"],
        "session_id": identity["session_id"],
        "log_id": identity["log_id"],
        "email": identity["email"],
        "user_resource_key": resource_key,
        "org_key": identity["org_key"],
        "task_id": identity["task_id"],
        "recorded_at": identity["recorded_at"],
        "duration_ms": identity["duration_ms"],
        "chunk_index": row.get("chunk_index", 0),
        "expected_chunk_count": row.get("expected_chunk_count", 1),
        "checked_at": checked_at,
    }
    row["state"] = STATE_FAILED
    row["phase"] = "remote_terminal_failure"
    return row


def all_terminal_remote_failure(rows: Any, services: ReceiptServices) -> bool:
    if not isinstance(rows, list) or not rows:
        return False
    first = rows[0]
    expected = first.get("expected_chunk_count", 1) if isinstance(first, dict) else None
    if type(expected) is not int or expected < 1 or len(rows) != expected:
        return False
    if not all(isinstance(row, dict) and services.journal_flags_valid(row)
               and services.is_terminal_remote_failure(row) for row in rows):
        return False
    indexes = [row.get("chunk_index", 0) for row in rows]
    if any(type(index) is not int for index in indexes) or set(indexes) != set(range(expected)):
        return False
    for row in rows:
        for key in ("account_email", "org_key", "session_id", "task_id", "campaign_context",
                    "expected_chunk_count"):
            if row.get(key, 0 if key == "chunk_index" else None) != first.get(
                    key, 0 if key == "chunk_index" else None):
                return False
    resource_keys = {row["remote_terminal_failure"]["user_resource_key"] for row in rows}
    return len({row.get("upload_id") for row in rows}) == expected and len(resource_keys) == 1


def is_remote_recovery_candidate(row: Any, services: ReceiptServices) -> bool:
    """Cheaply select native failed receipts requiring one authenticated GET.

    This deliberately includes failed completion-stage rows, which cannot be
    retried by the ordinary transport selector. It performs no file I/O.
    """
    if not isinstance(row, dict) or row.get("state") != STATE_FAILED:
        return False
    if row.get("phase") not in {"complete", "completing"}:
        return services.is_pending_transport(row)
    try:
        services.remote_recovery_identity(row)
        return True
    except services.upload_error:
        return False



__all__ = ["_terminal_failure_marker", "is_terminal_remote_failure", "mark_remote_terminal_failure", "all_terminal_remote_failure", "is_remote_recovery_candidate"]
