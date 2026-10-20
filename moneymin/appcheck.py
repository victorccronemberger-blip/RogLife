"""Compatibility API for the mobile App Check subsystem.

Implementation: moneymin.mobile.appcheck. Historical state and hook names remain
here so existing callers and late-bound tests share exactly one cache and lock.
"""
from __future__ import annotations
import logging
import os
import threading
import time
import urllib.error
from contextlib import contextmanager
from pathlib import Path
from . import config, tls
from .mobile import configuration
from .mobile.appcheck import errors, exchange, jwt, persistence
from .mobile.appcheck.runtime import AppCheckRuntime
from .mobile.android.provider import legacy_provider
from .mobile.appcheck.errors import (
    _FAILURE_NOT_CONFIGURED,
    _FAILURE_ATTESTATION,
    _FAILURE_DEVICE,
    _FAILURE_PERMISSION,
    _FAILURE_QUOTA,
    _FAILURE_INVALID,
    _FAILURE_EXCHANGE,
    _ATTESTATION_COOLDOWN_S,
    _QUOTA_COOLDOWN_S,
    _NON_RETRYABLE_FAILURES,
    _FAILURE_MESSAGES,
    _FIREBASE_STATUSES,
    AppCheckExchangeError,
)

logger = logging.getLogger(__name__)
_lock = threading.RLock()
_identity = None
_token = None
_expires = 0.0
_retry_at = 0.0
_last_failure: str | None = None
_last_diagnostic: dict | None = None


def _settings():
    return configuration.settings(os.environ)


def _configured_bearer() -> str:
    """Optional administrator-supplied App Check JWT (not a debug secret)."""
    return configuration.configured_bearer(os.environ)


def _settings_digest(values: tuple[str, ...]) -> str:
    return configuration.settings_digest(config.ROOT, values, _configured_bearer())


def _state_path() -> Path:
    return configuration.state_path(config.DATA_DIR)


def _lock_path() -> Path:
    return configuration.lock_path(config.DATA_DIR)


def _settings_identity(values: tuple[str, ...], bearer: str) -> bytes:
    return configuration.settings_identity(config.ROOT, values, bearer)


def _is_paused() -> bool:
    return configuration.is_paused(config.DATA_DIR)


def _bearer_still_valid(token: str) -> bool:
    """Accept only compact JWTs with a future exp; signature is server-verified."""
    return jwt.bearer_still_valid(token, clock=time.time)


def _bearer_left_s(token: str) -> float | None:
    """Remaining seconds of a compact JWT exp; None when shape/exp is unusable."""
    return jwt.bearer_left_s(token, clock=time.time)


def _load_persisted_state(digest: str) -> dict | None:
    return persistence.load_state(_state_path(), digest, clock=time.time)


def _save_persisted_state(digest: str, failure: str | None, cooldown_s: float,
                          *, http_status: int | None = None,
                          firebase_status: str | None = None) -> None:
    persistence.save_state(_state_path(), digest, failure, cooldown_s, clock=time.time,
                           http_status=http_status, firebase_status=firebase_status)


@contextmanager
def _exchange_file_lock():
    """Serialize debug-token exchanges across local processes."""
    with persistence.exchange_file_lock(_lock_path()):
        yield


def _exchange_http_diagnostic(exc: urllib.error.HTTPError) -> dict:
    return errors._exchange_http_diagnostic(exc)


def _classify_exchange_http_error(exc: urllib.error.HTTPError) -> str:
    """Map Firebase exchange HTTP errors to stable local categories without bodies."""
    return _exchange_http_diagnostic(exc)["category"]


def _exchange(project, app, api_key, secret):
    return exchange.exchange(project, app, api_key, secret,
                             urlopen=tls.urlopen, diagnostic=_exchange_http_diagnostic)


def is_app_check_rejection(text: str) -> bool:
    """Only explicit markers qualify; a generic 401/403 is not App Check evidence."""
    return errors.is_app_check_rejection(text)


def is_app_check_blocked_reason(value: object) -> bool:
    """True only for the explicit Minute ``X-Blocked-Reason: app_check`` marker."""
    return errors.is_app_check_blocked_reason(value)


class _LegacyState:
    """Access the one historical state; never mirror or synchronize a second cache."""

    @property
    def lock(self):
        return _lock

    @property
    def identity(self):
        return _identity

    @identity.setter
    def identity(self, value):
        global _identity
        _identity = value

    @property
    def token(self):
        return _token

    @token.setter
    def token(self, value):
        global _token
        _token = value

    @property
    def expires(self):
        return _expires

    @expires.setter
    def expires(self, value):
        global _expires
        _expires = value

    @property
    def retry_at(self):
        return _retry_at

    @retry_at.setter
    def retry_at(self, value):
        global _retry_at
        _retry_at = value

    @property
    def last_failure(self):
        return _last_failure

    @last_failure.setter
    def last_failure(self, value):
        global _last_failure
        _last_failure = value

    @property
    def last_diagnostic(self):
        return _last_diagnostic

    @last_diagnostic.setter
    def last_diagnostic(self, value):
        global _last_diagnostic
        _last_diagnostic = value


class _LegacyPorts:
    """Resolve compatibility hooks at call time, including post-import patches."""

    @property
    def time(self):
        return time

    @property
    def logger(self):
        return logger

    def device_provider(self):
        return legacy_provider()

    @property
    def _bearer_still_valid(self):
        return _bearer_still_valid

    @property
    def _bearer_left_s(self):
        return _bearer_left_s

    @property
    def _configured_bearer(self):
        return _configured_bearer

    @property
    def _exchange(self):
        return _exchange

    @property
    def _exchange_file_lock(self):
        return _exchange_file_lock

    @property
    def _is_paused(self):
        return _is_paused

    @property
    def _load_persisted_state(self):
        return _load_persisted_state

    @property
    def _restore_persisted_failure(self):
        return _restore_persisted_failure

    @property
    def _save_persisted_state(self):
        return _save_persisted_state

    @property
    def _select_identity(self):
        return _select_identity

    @property
    def _set_failure_diagnostic(self):
        return _set_failure_diagnostic

    @property
    def _settings(self):
        return _settings

    @property
    def _settings_digest(self):
        return _settings_digest

    @property
    def _settings_identity(self):
        return _settings_identity

    @property
    def _state_path(self):
        return _state_path

    @property
    def app_check_status(self):
        return app_check_status

    @property
    def last_mint_diagnostic(self):
        return last_mint_diagnostic

    @property
    def mint_app_check_token(self):
        return mint_app_check_token


_runtime = AppCheckRuntime(_LegacyState(), _LegacyPorts())

def clear_cache() -> None:
    """Forget process-local credentials. Never read or delete installation files."""
    return _runtime.clear_cache()


def is_app_check_configured() -> bool:
    return _runtime.is_app_check_configured()


def last_mint_failure() -> str | None:
    """Return a sanitized mint failure category, or None after a successful mint."""
    return _runtime.last_mint_failure()


def _select_identity(values: tuple[str, ...], bearer: str) -> None:
    return _runtime._select_identity(values, bearer)


def _restore_persisted_failure(digest: str, persisted: dict | None = None) -> None:
    """Restore only sanitized failure metadata; this helper never mints."""
    return _runtime._restore_persisted_failure(digest, persisted)


def _set_failure_diagnostic(category: str, *, http_status: int | None = None,
                            firebase_status: str | None = None,
                            retry_after_unix: float | None = None) -> None:
    return _runtime._set_failure_diagnostic(category, http_status=http_status, firebase_status=firebase_status, retry_after_unix=retry_after_unix)


def last_mint_diagnostic() -> dict | None:
    """Return the local sanitized mint diagnosis, restoring persisted state offline."""
    return _runtime.last_mint_diagnostic()


def app_check_status() -> dict:
    """Return a local-only App Check snapshot without minting or network access."""
    return _runtime.app_check_status()


def mint_app_check_token(
    force_refresh: bool = False,
    *,
    bypass_live_session: bool = False,
) -> str | None:
    return _runtime.mint_app_check_token(force_refresh, bypass_live_session=bypass_live_session)


def get_app_check_header() -> dict[str, str]:
    return _runtime.get_app_check_header()


def ensure_app_check_fresh(*, min_left_s: int | None = None) -> dict:
    """Renew App Check when remaining life is below the campaign floor.

    Device mode streams from the phone and escalates to a Minute restart when the
    live session is unusable. Firebase debug-exchange cooldowns do not block that
    ADB path. Returns the post-check local status without raising.
    """
    return _runtime.ensure_app_check_fresh(min_left_s=min_left_s)


def renew_app_check_for_batch() -> dict:
    """Renew App Check from the phone before a multi-account clip batch.

    Always re-reads SharedPreferences via ADB (ignores in-process session age).
    Escalates to a Minute restart when the live phone token is still unusable.
    Campaign/CLI callers often omit SOURCE; activate device defaults here unless
    the operator explicitly disabled capture.
    """
    return _runtime.renew_app_check_for_batch()
