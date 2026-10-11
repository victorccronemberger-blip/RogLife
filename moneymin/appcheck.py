"""Optional App Check debug provider for an administrator-authorized Firebase app.

No token capture, attestation bypass, or persistent credential cache. Missing
configuration sends no header; a server rejection must remain a rejection.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from . import config, tls

logger = logging.getLogger(__name__)
_lock = threading.RLock()
_identity = None
_token = None
_expires = 0.0
_retry_at = 0.0
# Sanitized mint outcome only: never store secrets, URLs, or response bodies.
_last_failure: str | None = None

_FAILURE_NOT_CONFIGURED = "not_configured"
_FAILURE_ATTESTATION = "attestation_failed"
_FAILURE_PERMISSION = "permission_denied"
_FAILURE_QUOTA = "quota_exhausted"
_FAILURE_INVALID = "invalid_response"
_FAILURE_EXCHANGE = "exchange_failed"
_ATTESTATION_COOLDOWN_S = 3600.0
_QUOTA_COOLDOWN_S = 70.0
_NON_RETRYABLE_FAILURES = {_FAILURE_ATTESTATION, _FAILURE_PERMISSION}


def _settings():
    return tuple(os.environ.get(key, "").strip() for key in (
        "FIREBASE_APP_CHECK_PROJECT_ID", "FIREBASE_APP_CHECK_APP_ID",
        "FIREBASE_APP_CHECK_API_KEY", "FIREBASE_APP_CHECK_DEBUG_TOKEN"))


def _configured_bearer() -> str:
    """Optional administrator-supplied App Check JWT (not a debug secret)."""
    return os.environ.get("FIREBASE_APP_CHECK_BEARER", "").strip()


def _bearer_still_valid(token: str) -> bool:
    """Accept only compact JWTs with a future exp; signature is server-verified."""
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        return False
    if any(ord(c) <= 32 or ord(c) >= 127 for c in token) or len(token) > 16384:
        return False
    try:
        import base64
        pad = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    except (ValueError, TypeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    exp = payload.get("exp")
    if type(exp) not in (int, float):
        return False
    # Small skew margin; server still enforces exact expiry.
    return float(exp) > time.time() + 30


def _settings_digest(values: tuple[str, ...]) -> str:
    bearer = _configured_bearer()
    return hashlib.sha256(
        json.dumps((str(config.ROOT), values, bool(bearer))).encode()
    ).hexdigest()[:24]


def _state_path() -> Path:
    return Path(config.DATA_DIR) / "appcheck_mint_state.json"


def _lock_path() -> Path:
    return Path(config.DATA_DIR) / "appcheck_exchange.lock"


def _load_persisted_state(digest: str) -> dict | None:
    path = _state_path()
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
    if failure not in {
        _FAILURE_ATTESTATION, _FAILURE_PERMISSION, _FAILURE_QUOTA, _FAILURE_EXCHANGE, _FAILURE_INVALID,
    }:
        return None
    if type(retry_after) not in (int, float):
        return None
    return {"failure": failure, "retry_after_unix": float(retry_after)}


def _save_persisted_state(digest: str, failure: str | None, cooldown_s: float) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if failure is None:
            if path.exists():
                path.unlink()
            return
        payload = {
            "settings_digest": digest,
            "failure": failure,
            "retry_after_unix": time.time() + cooldown_s,
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        # Local coordination only; mint behavior remains correct in-process.
        pass


def clear_cache() -> None:
    """Forget process-local credentials. Never read or delete installation files."""
    global _identity, _token, _expires, _retry_at, _last_failure
    with _lock:
        _identity, _token, _expires, _retry_at, _last_failure = None, None, 0.0, 0.0, None
        try:
            path = _state_path()
            if path.exists():
                path.unlink()
        except OSError:
            pass


def is_app_check_configured() -> bool:
    return all(_settings())


@contextmanager
def _exchange_file_lock():
    """Serialize debug-token exchanges across local processes."""
    path = _lock_path()
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


def last_mint_failure() -> str | None:
    """Return a sanitized mint failure category, or None after a successful mint."""
    with _lock:
        return _last_failure


def _classify_exchange_http_error(exc: urllib.error.HTTPError) -> str:
    """Map Firebase exchange HTTP errors to stable local categories without bodies."""
    status = int(getattr(exc, "code", 0) or 0)
    try:
        raw = exc.read(4096)
    except Exception:
        raw = b""
    message = ""
    firebase_status = ""
    try:
        payload = json.loads(raw.decode("utf-8", "replace")) if raw else {}
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            message = str(error.get("message") or "")
            firebase_status = str(error.get("status") or "")
    except (ValueError, TypeError, UnicodeError):
        pass
    normalized = re.sub(r"[ _-]", "", f"{message} {firebase_status}").casefold()
    if status == 403 and "appattestationfailed" in normalized:
        return _FAILURE_ATTESTATION
    if status == 403:
        # Permission denial alone does not identify the failed authorization rule.
        return _FAILURE_PERMISSION
    if status == 429 or "resourceexhausted" in normalized or "quota" in normalized:
        return _FAILURE_QUOTA
    return _FAILURE_EXCHANGE


def _exchange(project, app, api_key, secret):
    path = '/'.join(urllib.parse.quote(part, safe='') for part in (project, app))
    project_path, app_path = path.split('/')
    url = (f"https://firebaseappcheck.googleapis.com/v1/projects/{project_path}"
           f"/apps/{app_path}:exchangeDebugToken?key={urllib.parse.quote(api_key, safe='')}")
    req = urllib.request.Request(url, method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"debugToken": secret}).encode())
    try:
        with tls.urlopen(req, timeout=10) as response:
            if response.status != 200:
                raise ValueError("exchange failed")
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError("oversized response")
            data = json.loads(raw)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(_classify_exchange_http_error(exc)) from None
    token, ttl = data.get("token"), data.get("ttl")
    if (not isinstance(token, str) or not token or len(token) > 16384
            or any(ord(c) <= 32 or ord(c) >= 127 for c in token)
            or not isinstance(ttl, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,9})?s", ttl)):
        raise ValueError("invalid token response")
    seconds = float(ttl[:-1])
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("invalid lifetime")
    return token, seconds


def mint_app_check_token(force_refresh: bool = False) -> str | None:
    global _identity, _token, _expires, _retry_at, _last_failure
    with _lock:
        values = _settings()
        bearer = _configured_bearer()
        # A changed or removed bearer must not survive in the debug-token cache.
        identity = hashlib.sha256(json.dumps((str(config.ROOT), values, bearer)).encode()).digest()
        digest = _settings_digest(values)
        if identity != _identity:
            _identity, _token, _expires, _retry_at, _last_failure = identity, None, 0.0, 0.0, None
            persisted = _load_persisted_state(digest)
            if persisted is not None:
                _last_failure = persisted["failure"]
                # Convert wall-clock retry into this process's monotonic clock.
                wait_s = max(0.0, persisted["retry_after_unix"] - time.time())
                _retry_at = time.monotonic() + wait_s
        if bearer:
            if _bearer_still_valid(bearer):
                _last_failure = None
                _token, _expires = bearer, time.monotonic() + 300.0
                return bearer
            _last_failure = _FAILURE_INVALID
            _token, _expires = None, 0.0
            return None
        if not all(values):
            # Optional integration: no warnings or network traffic when unconfigured.
            _last_failure = _FAILURE_NOT_CONFIGURED
            return None
        # Local operators can pause exchanges while Firebase debug-token quota
        # recovers (for example concurrent tools sharing the same project).
        try:
            if (Path(config.DATA_DIR) / "appcheck_pause.flag").exists():
                return None
        except OSError:
            pass
        now = time.monotonic()
        if not force_refresh and _token and now < _expires:
            _last_failure = None
            return _token
        # A denied attestation is not a transient network failure. Reusing the
        # same settings on every ordinary request cannot authorize that token.
        # A settings change resets this outcome above; an explicit refresh may
        # recheck external registration after the existing cooldown.
        if _last_failure in _NON_RETRYABLE_FAILURES and not force_refresh:
            return None
        if now < _retry_at:
            return None
        _token, _expires = None, 0.0
        try:
            with _exchange_file_lock():
                # After a cold start, another process may have recorded attestation
                # denial. Ordinary callers still must not hammer Firebase.
                if not force_refresh:
                    persisted = _load_persisted_state(digest)
                    if persisted and persisted["failure"] in _NON_RETRYABLE_FAILURES:
                        _last_failure = persisted["failure"]
                        return None
                token, ttl = _exchange(*values)
            # Measure from before the request; latency must not extend the TTL.
            expires = now + ttl - min(300.0, ttl * 0.1)
            if expires <= time.monotonic():
                raise ValueError("expired response")
            _token, _expires, _last_failure = token, expires, None
            _save_persisted_state(digest, None, 0.0)
            return token
        except Exception as exc:
            # Do not log URLs, response bodies, exceptions or credential values.
            category = str(exc) if isinstance(exc, RuntimeError) and str(exc) in {
                _FAILURE_ATTESTATION, _FAILURE_PERMISSION, _FAILURE_QUOTA, _FAILURE_EXCHANGE,
            } else (
                _FAILURE_INVALID if isinstance(exc, ValueError) else _FAILURE_EXCHANGE
            )
            _last_failure = category
            cooldown = (
                _ATTESTATION_COOLDOWN_S if category in _NON_RETRYABLE_FAILURES
                else _QUOTA_COOLDOWN_S if category == _FAILURE_QUOTA
                else 60.0
            )
            _retry_at = time.monotonic() + cooldown
            _save_persisted_state(digest, category, cooldown)
            if category in _NON_RETRYABLE_FAILURES:
                logger.warning(
                    "App Check indisponível (%s); repetição automática suspensa "
                    "para esta configuração. A política do servidor permanece válida.",
                    category,
                )
            else:
                logger.warning(
                    "App Check indisponível (%s); nova tentativa após %.0f segundos. "
                    "A política do servidor permanece válida.",
                    category,
                    cooldown,
                )
            return None


def get_app_check_header() -> dict[str, str]:
    token = mint_app_check_token()
    return {"X-Firebase-AppCheck": token} if token else {}


def is_app_check_rejection(text: str) -> bool:
    """Only explicit markers qualify; a generic 401/403 is not App Check evidence."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    detail = data.get("detail", data)
    if isinstance(detail, dict):
        detail = ' '.join(str(detail.get(k, '')) for k in ('error', 'code', 'message'))
    if not isinstance(detail, str):
        return False
    normalized = re.sub(r"[ _-]", "", detail).casefold()
    return 'appcheck' in normalized


def is_app_check_blocked_reason(value: object) -> bool:
    """True only for the explicit Minute ``X-Blocked-Reason: app_check`` marker."""
    return isinstance(value, str) and value.casefold() == "app_check"
