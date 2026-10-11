"""Cross-process leases for per-session upload and recovery protocols."""
from __future__ import annotations

import hashlib
import inspect
import uuid
from collections.abc import Callable
from contextlib import ExitStack
from functools import wraps
from pathlib import Path
from typing import Any


class LeaseServices:
    """Explicit collaborators supplied by the upload compatibility facade."""

    def __init__(self, *, data_dir: Path, operation_lease: Callable[..., Any],
                 operation_lease_error: type[Exception], campaign_state_lease: Callable[..., Any],
                 upload_error: type[Exception], validate_session_id: Callable[[Any], Any],
                 list_sidecars: Callable[[], list[dict[str, Any]]]) -> None:
        self.data_dir = data_dir
        self.operation_lease = operation_lease
        self.operation_lease_error = operation_lease_error
        self.campaign_state_lease = campaign_state_lease
        self.upload_error = upload_error
        self.validate_session_id = validate_session_id
        self.list_sidecars = list_sidecars


def _path(data_dir: Path, session_id: str) -> Path:
    digest = hashlib.sha256(session_id.encode("utf8")).hexdigest()
    return data_dir / "upload-session-leases" / f"{digest}.lock"


def session_protocol(fn: Callable[..., Any], services: Callable[[], LeaseServices]) -> Callable[..., Any]:
    signature = inspect.signature(fn)

    @wraps(fn)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        deps = services()
        bound = signature.bind(*args, **kwargs)
        session_id = bound.arguments.get("session_id")
        if session_id is None:
            session_id = str(uuid.uuid4())
            bound.arguments["session_id"] = session_id
        deps.validate_session_id(session_id)
        try:
            with deps.campaign_state_lease(), deps.operation_lease(_path(deps.data_dir, session_id)):
                return fn(*bound.args, **bound.kwargs)
        except deps.operation_lease_error:
            raise deps.upload_error(
                "A sessão já possui uma operação local em andamento.",
                transient=True, phase="preflight",
            ) from None
    return guarded


def pending_protocol(fn: Callable[..., Any], services: Callable[[], LeaseServices]) -> Callable[..., Any]:
    signature = inspect.signature(fn)

    @wraps(fn)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        deps = services()
        bound = signature.bind(*args, **kwargs)
        arguments = bound.arguments
        session = arguments["session"]
        owner = arguments.get("account_email") or getattr(session, "email", None)
        if owner is not None and (not isinstance(owner, str) or not owner.strip()):
            raise deps.upload_error("Conta de retomada inválida.", phase="preflight")
        org = arguments.get("required_org_key")
        selected = arguments.get("session_ids")
        try:
            with deps.campaign_state_lease(), ExitStack() as stack:
                rows = list(deps.list_sidecars())
                session_ids = sorted({
                    row["session_id"] for row in rows
                    if (owner is None or (isinstance(row.get("account_email"), str)
                        and row["account_email"].strip().casefold() == owner.strip().casefold()))
                    and (org is None or row.get("org_key") == org)
                    and (selected is None or row.get("session_id") in selected)
                })
                for session_id in session_ids:
                    if not isinstance(session_id, str):
                        raise deps.upload_error("Identidade de retomada inválida.", phase="preflight")
                    stack.enter_context(deps.operation_lease(_path(deps.data_dir, session_id)))
                return fn(*args, **kwargs)
        except deps.operation_lease_error:
            raise deps.upload_error(
                "Uma sessão selecionada está em uso; preserve a retomada.",
                transient=True, phase="preflight",
            ) from None
    return guarded


__all__ = ["LeaseServices", "pending_protocol", "session_protocol"]
