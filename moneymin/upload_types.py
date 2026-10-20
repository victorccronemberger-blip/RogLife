"""Upload result types and public acceptance predicate facade."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .delivery.acceptance import (
    STATE_CREATING, STATE_TRANSPORT, STATE_COMPLETING, STATE_DONE, STATE_FAILED,
    STATE_RETRY_LATE, STATE_LOSS, STATE_QUARANTINE, TRANSIENT_STATES,
    journal_flags_valid, journal_evaluation_confirmed, is_pending_finalization,
    is_pending_evaluation, journal_delivery_confirmed,
)


class UploadError(RuntimeError):
    """Falha em uma etapa do upload ou da finalização da sessão."""

    attempts: int | None

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        transient: bool | None = None,
        blocked_reason: str | None = None,
        phase: str | None = None,
        review_required: bool = False,
        appcheck_cause: str | None = None,
        appcheck_message: str | None = None,
        request_not_sent: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.transient = transient
        self.blocked_reason = blocked_reason
        self.phase = phase
        self.review_required = review_required
        self.appcheck_cause = appcheck_cause
        self.appcheck_message = appcheck_message
        # Only the request boundary can prove that this operation was not sent.
        self.request_not_sent = request_not_sent is True
        self.attempts = None

    @property
    def authentication_required(self) -> bool:
        reason = self.blocked_reason.casefold() if isinstance(self.blocked_reason, str) else None
        if reason == "app_check":
            # Local ADB capture miss before any Minute write can be retried by the
            # campaign without treating it as an operator authentication halt.
            if (self.request_not_sent and self.transient is True
                    and self.appcheck_cause == "device_capture_failed"):
                return False
            return True
        return self.status_code == 401 and not reason

    @property
    def retryable(self) -> bool:
        """Somente rede, timeout, 408/429 e 5xx merecem nova tentativa."""
        if self.review_required:
            return False
        # Minute may surface App Check as HTTP 429; keep it non-retryable.
        if self.authentication_required:
            return False
        if self.transient is not None:
            return self.transient
        if self.status_code is None:
            return True
        return self.status_code in (408, 429) or self.status_code >= 500


@dataclass
class ChunkResult:
    """Resultado de um chunk individual dentro de uma sessão."""

    upload_id: str
    chunk_index: int
    log_id: str
    blob_path: str
    size_bytes: int
    duration_ms: int
    raw_create: dict[str, Any] = field(default_factory=dict)
    raw_complete: dict[str, Any] = field(default_factory=dict)
    evaluate_result: dict[str, Any] | None = None
    state: str = STATE_DONE
    attempts: int = 1
    error: str | None = None
    sidecar_blob_path: str = ""
    sidecar_size_bytes: int = 0
    authentication_required: bool = False
    review_required: bool = False
    blocked_reason: str | None = None
    appcheck_cause: str | None = None
    appcheck_message: str | None = None
    request_not_sent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "upload_id": self.upload_id,
            "chunk_index": self.chunk_index,
            "log_id": self.log_id,
            "blob_path": self.blob_path,
            "size_bytes": self.size_bytes,
            "duration_ms": self.duration_ms,
            "raw_create": self.raw_create,
            "raw_complete": self.raw_complete,
            "evaluate_result": self.evaluate_result,
            "state": self.state,
            "attempts": self.attempts,
            "error": self.error,
            "sidecar_blob_path": self.sidecar_blob_path,
            "sidecar_size_bytes": self.sidecar_size_bytes,
            **({"authentication_required": True} if self.authentication_required else {}),
            **({"review_required": True} if self.review_required else {}),
            **({"blocked_reason": self.blocked_reason} if self.blocked_reason else {}),
            **({"appcheck_cause": self.appcheck_cause} if self.appcheck_cause else {}),
            **({"appcheck_message": self.appcheck_message} if self.appcheck_message else {}),
            **({"request_not_sent": True} if self.request_not_sent is True else {}),
        }


@dataclass
class UploadResult:
    """Resultado de uma sessão de upload completa (um ou mais chunks)."""

    session_id: str
    org_key: str
    task_id: str | None
    chunks: list[ChunkResult] = field(default_factory=list)
    finalized: bool = False
    finalize_status: int | None = None
    total_size_bytes: int = 0
    total_duration_ms: int = 0
    recorded_at: str = ""
    authentication_required: bool = False
    review_required: bool = False
    blocked_reason: str | None = None
    appcheck_cause: str | None = None
    appcheck_message: str | None = None

    @property
    def upload_id(self) -> str:
        return self.chunks[0].upload_id if self.chunks else ""

    @property
    def blob_path(self) -> str:
        return self.chunks[0].blob_path if self.chunks else ""

    @property
    def log_id(self) -> str:
        return self.chunks[0].log_id if self.chunks else ""

    @property
    def size_bytes(self) -> int:
        return self.total_size_bytes

    @property
    def duration_ms(self) -> int:
        return self.total_duration_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "org_key": self.org_key,
            "task_id": self.task_id,
            "finalized": self.finalized,
            "finalize_status": self.finalize_status,
            "total_size_bytes": self.total_size_bytes,
            "total_duration_ms": self.total_duration_ms,
            "recorded_at": self.recorded_at,
            "chunks": [chunk.to_dict() for chunk in self.chunks],
            **({"authentication_required": True} if self.authentication_required else {}),
            **({"review_required": True} if self.review_required else {}),
            **({"blocked_reason": self.blocked_reason} if self.blocked_reason else {}),
            **({"appcheck_cause": self.appcheck_cause} if self.appcheck_cause else {}),
            **({"appcheck_message": self.appcheck_message} if self.appcheck_message else {}),
        }


__all__ = [
    "ChunkResult",
    "STATE_COMPLETING",
    "STATE_CREATING",
    "STATE_DONE",
    "STATE_FAILED",
    "STATE_LOSS",
    "STATE_QUARANTINE",
    "STATE_RETRY_LATE",
    "STATE_TRANSPORT",
    "TRANSIENT_STATES",
    "UploadError",
    "UploadResult",
    "is_pending_finalization",
    "journal_evaluation_confirmed",
    "journal_flags_valid",
]
