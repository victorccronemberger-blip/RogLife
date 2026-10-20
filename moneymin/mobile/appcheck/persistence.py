"""Sanitized cooldown metadata and interprocess exchange lock. No JWT storage."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import json
import math
from .errors import (
    _FAILURE_NOT_CONFIGURED,
    _FAILURE_ATTESTATION,
    _FAILURE_PERMISSION,
    _FAILURE_QUOTA,
    _FAILURE_INVALID,
    _FAILURE_EXCHANGE,
    _ATTESTATION_COOLDOWN_S,
    _FAILURE_MESSAGES,
    _FIREBASE_STATUSES,
)

def load_state(path: Path, digest: str, *, clock) -> dict | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("settings_digest") != digest:
        return None
    failure = raw.get("failure")
    retry_after = raw.get("retry_after_unix")
    if not isinstance(failure, str) or failure not in {
        _FAILURE_ATTESTATION, _FAILURE_PERMISSION, _FAILURE_QUOTA, _FAILURE_EXCHANGE, _FAILURE_INVALID,
    }:
        return None
    if type(retry_after) not in (int, float):
        return None
    try:
        retry_after = float(retry_after)
    except (OverflowError, ValueError):
        return None
    if (not math.isfinite(retry_after) or retry_after < 0
            or retry_after - clock() > _ATTESTATION_COOLDOWN_S + 60):
        return None
    http_status = raw.get("http_status")
    if type(http_status) is not int or not 100 <= http_status <= 599:
        http_status = None
    firebase_status = raw.get("firebase_status")
    if not isinstance(firebase_status, str) or firebase_status not in _FIREBASE_STATUSES:
        firebase_status = None
    # Old files have only failure/retry_after_unix. Never trust or surface a
    # message copied from disk; the public text is selected from our own map.
    return {
        "failure": failure,
        "retry_after_unix": retry_after,
        "http_status": http_status,
        "firebase_status": firebase_status,
        "message": _FAILURE_MESSAGES[failure],
    }

def save_state(path: Path, digest: str, failure: str | None, cooldown_s: float,
                          *, clock, http_status: int | None = None,
                          firebase_status: str | None = None) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if failure is None:
            if path.exists():
                path.unlink()
            return
        if not isinstance(failure, str) or failure not in _FAILURE_MESSAGES or failure == _FAILURE_NOT_CONFIGURED:
            failure = _FAILURE_EXCHANGE
        if (type(http_status) is not int or not 100 <= http_status <= 599):
            http_status = None
        if not isinstance(firebase_status, str) or firebase_status not in _FIREBASE_STATUSES:
            firebase_status = None
        retry_after = clock() + cooldown_s
        if not math.isfinite(retry_after) or retry_after < 0:
            return
        payload = {
            "settings_digest": digest,
            "failure": failure,
            "retry_after_unix": retry_after,
            "http_status": http_status,
            "firebase_status": firebase_status,
            "message": _FAILURE_MESSAGES[failure],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        # Local coordination only; mint behavior remains correct in-process.
        pass

@contextmanager
def exchange_file_lock(path: Path):
    """Serialize debug-token exchanges across local processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = open(path, "a+b")
    locked = False
    try:
        try:
            import msvcrt
            # Blocking lock: one exchange at a time on this machine.
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            locked = True
        except ImportError:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            locked = True
        yield
    finally:
        if locked:
            try:
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            except ImportError:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        stream.close()
