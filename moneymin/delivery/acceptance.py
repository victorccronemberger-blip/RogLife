"""Durable receipt and evaluation acceptance rules.

These predicates depend only on journal data. They deliberately never infer
delivery from a finalized flag when a durable upload receipt is absent.
"""
from __future__ import annotations

import re
from typing import Any

STATE_CREATING = "creating"
STATE_TRANSPORT = "transport"
STATE_COMPLETING = "completing"
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_RETRY_LATE = "retry_late"
STATE_LOSS = "loss"
STATE_QUARANTINE = "quarantine"

TRANSIENT_STATES = {STATE_CREATING, STATE_TRANSPORT, STATE_COMPLETING, STATE_RETRY_LATE}


def failure_diagnostics(error: Any = None) -> dict[str, Any]:
    """Carry a typed failure across results/checkpoints, clearing stale proof."""
    reason = getattr(error, "blocked_reason", None)
    return {
        "authentication_required": getattr(error, "authentication_required", False) is True,
        "review_required": getattr(error, "review_required", False) is True,
        "blocked_reason": reason.casefold() if isinstance(reason, str) else None,
        "appcheck_cause": getattr(error, "appcheck_cause", None),
        "appcheck_message": getattr(error, "appcheck_message", None),
        "request_not_sent": getattr(error, "request_not_sent", False) is True,
    }


def evaluation_failure_retryable(error: Any, status: Any) -> bool:
    """Require explicit local evidence or a recoverable evaluation response."""
    details = failure_diagnostics(error)
    if details["review_required"]:
        return False
    if details["request_not_sent"] and details["authentication_required"]:
        return True
    return type(status) is int and (status in {-1, 408, 429} or 500 <= status <= 599
                                   or (details["authentication_required"] and status in {401, 403}))


def journal_flags_valid(row: dict[str, Any]) -> bool:
    """Persisted flags and state labels must keep their declared types."""
    flags = ("finalized", "finalize_requested", "evaluation_required",
             "evaluation_verified", "campaign_reconciled", "register_first",
             "suppress_per_chunk_catbear", "create_attempted", "native_response_schema",
             "remote_fail_attempted", "remote_fail_confirmed", "upload_delete_attempted",
             "upload_delete_confirmed", "session_delete_attempted", "session_delete_confirmed",
             "authentication_required", "request_not_sent", "review_required")
    return isinstance(row, dict) and all(
        type(row[key]) is bool for key in flags if key in row) and all(
        isinstance(row[key], str) for key in ("state", "phase") if key in row)


def journal_evaluation_confirmed(row: dict[str, Any]) -> bool:
    """Whether the stored quality gate permits confirmation of a receipt."""
    if not journal_flags_valid(row) or row.get("phase") in {"evaluation_review", "quality_rejected"}:
        return False
    if "evaluation_required" in row:
        return row["evaluation_required"] is False or row.get("evaluation_verified") is True
    if "evaluation_verified" in row:
        return row["evaluation_verified"] is True
    return True


def is_pending_finalization(row: dict[str, Any]) -> bool:
    """A complete chunk can resume evaluation/finalize without resending bytes."""
    upload_id = row.get("upload_id") if isinstance(row, dict) else None
    return (journal_flags_valid(row) and row.get("state") == STATE_DONE
            and row.get("phase") == "done" and row.get("finalize_requested") is True
            and row.get("finalized") is not True
            and isinstance(upload_id, str) and bool(upload_id.strip()))


def is_pending_evaluation(row: dict[str, Any]) -> bool:
    """Only a known receipt with an unavailable quality check can be retried."""
    if (not journal_flags_valid(row) or row.get("state") != STATE_QUARANTINE
            or row.get("phase") != "evaluation_review"
            or row.get("finalize_requested") is not True
            or row.get("finalized") is True
            or row.get("evaluation_required") is not True
            or row.get("evaluation_verified") is not False
            or not isinstance(row.get("upload_id"), str)
            or not row["upload_id"].strip()):
        return False
    upload_id = row["upload_id"]
    if (upload_id in {".", ".."} or any(char.isspace() or ord(char) < 32
            or ord(char) == 127 or char in "/\\?#%" for char in upload_id)):
        return False
    if row.get("review_required") is True:
        return False
    if (row.get("request_not_sent") is True
            and row.get("authentication_required") is True):
        return True
    if "evaluation_http_status" in row:
        status = row["evaluation_http_status"]
    else:
        match = re.fullmatch(
            r"Avaliação inconclusiva \(HTTP (-1|408|429|5\d{2})\); envio preservado para revisão\.",
            str(row.get("error") or ""))
        status = int(match.group(1)) if match else None
    return type(status) is int and (status in {-1, 408, 429} or 500 <= status <= 599
                                   or (row.get("authentication_required") is True and status in {401, 403}))


def journal_delivery_confirmed(row: dict[str, Any]) -> bool:
    """A durable upload ID plus completed state and quality gate confirm delivery."""
    upload_id = row.get("upload_id") if isinstance(row, dict) else None
    return (journal_evaluation_confirmed(row)
            and row.get("state") == STATE_DONE and row.get("finalized") is True
            and row.get("authentication_required") is not True
            and isinstance(upload_id, str) and bool(upload_id.strip()))


__all__ = [
    "STATE_CREATING", "STATE_TRANSPORT", "STATE_COMPLETING", "STATE_DONE",
    "STATE_FAILED", "STATE_RETRY_LATE", "STATE_LOSS", "STATE_QUARANTINE",
    "TRANSIENT_STATES", "journal_flags_valid", "journal_evaluation_confirmed",
    "is_pending_finalization", "is_pending_evaluation", "journal_delivery_confirmed",
]
