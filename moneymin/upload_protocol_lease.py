"""Compatibility decorators backed by delivery.lease.

The delivery layer receives a services object and never imports upload.py.
Resolve callbacks per invocation so monkeypatches on the legacy facade remain
visible to existing tests and callers.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import config
from .campaign_state import campaign_state_lease
from .delivery import lease as _lease
from .operation_lease import operation_lease, OperationLeaseError
from .upload_types import UploadError


def _services() -> _lease.LeaseServices:
    from . import upload
    return _lease.LeaseServices(
        data_dir=config.DATA_DIR,
        operation_lease=operation_lease,
        operation_lease_error=OperationLeaseError,
        campaign_state_lease=campaign_state_lease,
        upload_error=upload.UploadError,
        validate_session_id=upload._sidecar_filename,
        list_sidecars=upload.list_sidecars,
    )


def _path(session_id: str) -> Path:
    return _lease._path(config.DATA_DIR, session_id)


def session_protocol(fn: Callable[..., Any]) -> Callable[..., Any]:
    return _lease.session_protocol(fn, _services)


def pending_protocol(fn: Callable[..., Any]) -> Callable[..., Any]:
    return _lease.pending_protocol(fn, _services)
