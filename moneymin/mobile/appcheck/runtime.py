"""App Check lifecycle. One injected state/lock; no imports of the legacy facade.

Device-provider calls stay outside the state lock. Debug exchanges keep their
process and file locks. Ports resolve the current compatibility hooks on every call.
"""
from __future__ import annotations
import json
from math import ceil
from .contracts import AppCheckState, AppCheckPorts
from .errors import (
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

class AppCheckRuntime:
    def __init__(self, state: AppCheckState, ports: AppCheckPorts):
        self.state = state
        self.ports = ports

    def clear_cache(self) -> None:
        """Reset process state and sanitized mint diagnostics; keep device storage."""
        with self.state.lock:
            self.state.identity, self.state.token, self.state.expires, self.state.retry_at, self.state.last_failure, self.state.last_diagnostic = (
                None, None, 0.0, 0.0, None, None)
            try:
                path = self.ports._state_path()
                if path.exists():
                    path.unlink()
            except OSError:
                pass

    def is_app_check_configured(self) -> bool:
        try:
            roglife_appcheck = self.ports.device_provider()
            if roglife_appcheck.device_mode_enabled():
                return True
        except Exception:
            pass
        return bool(self.ports._configured_bearer()) or all(self.ports._settings())

    def last_mint_failure(self) -> str | None:
        """Return a sanitized mint failure category, or None after a successful mint."""
        with self.state.lock:
            return self.state.last_failure

    def _select_identity(self, values: tuple[str, ...], bearer: str) -> None:
        identity = self.ports._settings_identity(values, bearer)
        if identity != self.state.identity:
            self.state.identity, self.state.token, self.state.expires = identity, None, 0.0
            self.state.last_failure, self.state.last_diagnostic, self.state.retry_at = None, None, 0.0
            self.ports._restore_persisted_failure(self.ports._settings_digest(values))

    def _restore_persisted_failure(self, digest: str, persisted: dict | None = None) -> None:
        """Restore only sanitized failure metadata; this helper never mints."""
        if persisted is None:
            persisted = self.ports._load_persisted_state(digest)
        if persisted is None:
            self.state.last_failure, self.state.last_diagnostic, self.state.retry_at = None, None, 0.0
            return
        self.state.last_failure = persisted["failure"]
        self.state.last_diagnostic = {
            "category": persisted["failure"],
            "http_status": persisted.get("http_status"),
            "firebase_status": persisted.get("firebase_status"),
            "message": persisted.get("message", _FAILURE_MESSAGES[persisted["failure"]]),
            "retry_after_unix": persisted["retry_after_unix"],
        }
        wait_s = max(0.0, persisted["retry_after_unix"] - self.ports.time.time())
        self.state.retry_at = self.ports.time.monotonic() + wait_s

    def _set_failure_diagnostic(self, category: str, *, http_status: int | None = None,
                                firebase_status: str | None = None,
                                retry_after_unix: float | None = None) -> None:
        if (not isinstance(category, str) or category not in _FAILURE_MESSAGES
                or category == _FAILURE_NOT_CONFIGURED):
            category = _FAILURE_EXCHANGE
        if type(http_status) is not int or not 100 <= http_status <= 599:
            http_status = None
        if not isinstance(firebase_status, str) or firebase_status not in _FIREBASE_STATUSES:
            firebase_status = None
        self.state.last_failure = category
        self.state.last_diagnostic = {
            "category": category,
            "http_status": http_status,
            "firebase_status": firebase_status,
            "message": _FAILURE_MESSAGES[category],
            "retry_after_unix": retry_after_unix,
        }

    def last_mint_diagnostic(self) -> dict | None:
        """Return the local sanitized mint diagnosis, restoring persisted state offline."""
        with self.state.lock:
            values = self.ports._settings()
            bearer = self.ports._configured_bearer()
            self.ports._select_identity(values, bearer)
            if self.state.last_diagnostic is None:
                return None
            result = dict(self.state.last_diagnostic)
            if self.state.retry_at:
                result["retry_after_seconds"] = max(
                    0, int(ceil(self.state.retry_at - self.ports.time.monotonic())))
            else:
                result["retry_after_seconds"] = 0
            result["retry_requires_explicit"] = (
                result["category"] in _NON_RETRYABLE_FAILURES)
            return result

    def app_check_status(self) -> dict:
        """Return a local-only App Check snapshot without minting or network access."""
        values = self.ports._settings()
        bearer = self.ports._configured_bearer()
        device = {}
        try:
            roglife_appcheck = self.ports.device_provider()
            device = roglife_appcheck.status_snapshot()
        except Exception:
            device = {"enabled": False, "token_available": False}
        configured = bool(device.get("enabled")) or bool(bearer) or all(values)
        configuration_present = (
            bool(device.get("enabled")) or bool(bearer) or any(values)
        )
        paused = self.ports._is_paused()
        diagnostic = self.ports.last_mint_diagnostic()
        token_available = False
        source = None
        if device.get("enabled") and device.get("token_available"):
            token_available = True
            source = "device"
        elif bearer:
            token_available = self.ports._bearer_still_valid(bearer)
            source = "bearer" if token_available else None
        elif all(values) and not paused:
            with self.state.lock:
                token_available = bool(self.state.token and self.ports.time.monotonic() < self.state.expires)
            source = "debug_exchange" if token_available else None
        retry_after_seconds = (
            diagnostic.get("retry_after_seconds") if diagnostic else 0)
        return {
            "configured": configured,
            "configuration_present": configuration_present,
            "configuration_pending": configuration_present and not configured,
            "token_available": token_available,
            "source": source,
            "device": device,
            "failure": diagnostic.get("category") if diagnostic else None,
            "diagnostic": diagnostic,
            "retry_after_seconds": int(retry_after_seconds or 0),
            "paused": paused,
        }

    def mint_app_check_token(self,
        force_refresh: bool = False,
        *,
        bypass_live_session: bool = False,
    ) -> str | None:

        # 1) Preferência: Play Integrity do aparelho físico (RogLife).
        # Captura adb fica FORA do lock para não bloquear outros callers.
        # Em modo device, NÃO cair no debug exchange (CREATE de produção rejeita).
        try:
            roglife_appcheck = self.ports.device_provider()
            if roglife_appcheck.device_mode_enabled():
                device_error = None
                device_token = None

                # Device JWT streams from the phone via ADB. Disk never authorizes.
                # bypass_live_session re-reads the phone even when the process still
                # holds a usable JWT (campaign batch boundaries).
                try:
                    device_token = roglife_appcheck.obtain_token(
                        force=force_refresh,
                        bypass_live_session=bypass_live_session or force_refresh,
                    )
                except Exception as exc:
                    device_error = exc
                    self.ports.logger.warning(
                        "App Check device indisponível (%s). Tentando renovar...",
                        type(exc).__name__,
                    )

                if device_token and self.ports._bearer_still_valid(device_token):
                    with self.state.lock:
                        self.state.last_failure, self.state.last_diagnostic = None, None
                        left = 300.0
                        try:
                            import base64
                            pad = "=" * (-len(device_token.split(".")[1]) % 4)
                            payload = json.loads(
                                base64.urlsafe_b64decode(device_token.split(".")[1] + pad)
                            )
                            exp = float(payload.get("exp"))
                            left = max(30.0, exp - self.ports.time.time() - 30.0)
                        except Exception:
                            pass
                        self.state.token, self.state.expires = device_token, self.ports.time.monotonic() + min(left, 3600.0)
                    return device_token
                # Bearer espelhado ainda pode servir se o JWT continuar válido.
                mirrored = self.ports._configured_bearer()
                if mirrored and self.ports._bearer_still_valid(mirrored):
                    with self.state.lock:
                        self.state.last_failure, self.state.last_diagnostic = None, None
                        self.state.token, self.state.expires = mirrored, self.ports.time.monotonic() + 300.0
                    return mirrored
                with self.state.lock:
                    # Device/ADB failure is not a Firebase debug-token attestation denial.
                    self.ports._set_failure_diagnostic(_FAILURE_DEVICE)
                    self.state.token, self.state.expires = None, 0.0
                if device_error is not None:
                    self.ports.logger.warning(
                        "Modo device ativo sem App Check utilizável (%s).",
                        type(device_error).__name__,
                    )
                return None
        except Exception as exc:
            self.ports.logger.warning(
                "App Check device provider falhou na carga (%s); tentando bearer/debug.",
                type(exc).__name__,
            )

        with self.state.lock:
            values = self.ports._settings()
            bearer = self.ports._configured_bearer()
            self.ports._select_identity(values, bearer)
            digest = self.ports._settings_digest(values)

            # 2) Bearer administrativo explícito (env / espelho do device).
            if bearer:
                if self.ports._bearer_still_valid(bearer):
                    self.state.last_failure, self.state.last_diagnostic = None, None
                    self.state.token, self.state.expires = bearer, self.ports.time.monotonic() + 300.0
                    return bearer
                self.ports._set_failure_diagnostic(_FAILURE_INVALID)
                self.state.token, self.state.expires = None, 0.0
                return None
            if not all(values):
                # Optional integration: no warnings or network traffic when unconfigured.
                self.state.last_failure = _FAILURE_NOT_CONFIGURED
                self.state.last_diagnostic = {
                    "category": _FAILURE_NOT_CONFIGURED,
                    "http_status": None,
                    "firebase_status": None,
                    "message": _FAILURE_MESSAGES[_FAILURE_NOT_CONFIGURED],
                    "retry_after_unix": None,
                }
                return None
            # Local operators can pause exchanges while Firebase debug-token quota
            # recovers (for example concurrent tools sharing the same project).
            if self.ports._is_paused():
                return None
            now = self.ports.time.monotonic()
            if not force_refresh and self.state.token and now < self.state.expires:
                self.state.last_failure, self.state.last_diagnostic = None, None
                return self.state.token
            # A denied attestation is not a transient network failure. Reusing the
            # same settings on every ordinary request cannot authorize that token.
            # A settings change resets this outcome above; an explicit refresh may
            # recheck external registration after the existing cooldown.
            if self.state.last_failure in _NON_RETRYABLE_FAILURES and not force_refresh:
                return None
            if now < self.state.retry_at:
                return None
            self.state.token, self.state.expires = None, 0.0
            try:
                with self.ports._exchange_file_lock():
                    # Another process may have recorded a fresh cooldown since this
                    # caller's local retry clock was last synchronized. Explicit
                    # refresh permits rechecking only after that persisted cooldown.
                    persisted = self.ports._load_persisted_state(digest)
                    if persisted:
                        persisted_wait = persisted["retry_after_unix"] - self.ports.time.time()
                        if persisted_wait > 0:
                            self.ports._restore_persisted_failure(digest, persisted)
                            return None
                        # Ordinary callers still cannot repeatedly retry permanent
                        # attestation/permission failures after the cooldown expires.
                        if (not force_refresh
                                and persisted["failure"] in _NON_RETRYABLE_FAILURES):
                            self.ports._restore_persisted_failure(digest, persisted)
                            return None
                    token, ttl = self.ports._exchange(*values)
                # Measure from before the request; latency must not extend the TTL.
                expires = now + ttl - min(300.0, ttl * 0.1)
                if expires <= self.ports.time.monotonic():
                    raise ValueError("expired response")
                self.state.token, self.state.expires, self.state.last_failure, self.state.last_diagnostic = token, expires, None, None
                self.ports._save_persisted_state(digest, None, 0.0)
                return token
            except Exception as exc:
                # Do not log URLs, response bodies, exceptions or credential values.
                category = exc.category if isinstance(exc, AppCheckExchangeError) else (
                    str(exc) if isinstance(exc, RuntimeError) and str(exc) in {
                    _FAILURE_ATTESTATION, _FAILURE_PERMISSION, _FAILURE_QUOTA, _FAILURE_EXCHANGE,
                    } else (
                    _FAILURE_INVALID if isinstance(exc, ValueError) else _FAILURE_EXCHANGE
                    )
                )
                http_status = exc.http_status if isinstance(exc, AppCheckExchangeError) else None
                firebase_status = exc.firebase_status if isinstance(exc, AppCheckExchangeError) else None
                cooldown = (
                    _ATTESTATION_COOLDOWN_S if category in _NON_RETRYABLE_FAILURES
                    else _QUOTA_COOLDOWN_S if category == _FAILURE_QUOTA
                    else 60.0
                )
                self.state.retry_at = self.ports.time.monotonic() + cooldown
                retry_after_unix = self.ports.time.time() + cooldown
                self.ports._set_failure_diagnostic(
                    category, http_status=http_status, firebase_status=firebase_status,
                    retry_after_unix=retry_after_unix)
                self.ports._save_persisted_state(
                    digest, category, cooldown, http_status=http_status,
                    firebase_status=firebase_status)
                if category in _NON_RETRYABLE_FAILURES:
                    self.ports.logger.warning(
                        "App Check indisponível (%s); repetição automática suspensa "
                        "para esta configuração. A política do servidor permanece válida.",
                        category,
                    )
                else:
                    self.ports.logger.warning(
                        "App Check indisponível (%s); nova tentativa após %.0f segundos. "
                        "A política do servidor permanece válida.",
                        category,
                        cooldown,
                    )
                return None

    def get_app_check_header(self) -> dict[str, str]:
        token = self.ports.mint_app_check_token()
        return {"X-Firebase-AppCheck": token} if token else {}

    def _available_token_left_s(self, status: dict) -> float | None:
        """Best remaining life of the credential the local status presents.

        Device mode may report the live phone token or the mirrored bearer set
        by the last extraction; the freshest measurable value decides whether
        the campaign floor is satisfied.
        """
        device = status.get("device") if isinstance(status.get("device"), dict) else {}
        candidates: list[float] = []
        left = device.get("left_s")
        if left is not None:
            try:
                candidates.append(float(left))
            except (TypeError, ValueError):
                pass
        bearer_left = self.ports._bearer_left_s(self.ports._configured_bearer())
        if isinstance(bearer_left, (int, float)) and not isinstance(bearer_left, bool):
            candidates.append(float(bearer_left))
        if not candidates:
            return None
        return max(candidates)

    def ensure_app_check_fresh(self, *, min_left_s: int | None = None) -> dict:
        """Renew App Check when remaining life is below the campaign floor.

        Device mode streams from the phone and escalates to a Minute restart when the
        live session is unusable. Firebase debug-exchange cooldowns do not block that
        ADB path. Returns the post-check local status without raising.
        """
        status = self.ports.app_check_status()
        device = status.get("device") if isinstance(status.get("device"), dict) else {}
        device_mode = bool(device.get("enabled"))
        try:
            floor = int(min_left_s if min_left_s is not None else device.get("min_left_s") or 600)
        except (TypeError, ValueError):
            floor = 600
        floor = max(60, floor)
        left_raw = device.get("left_s")
        try:
            left_s = float(left_raw) if left_raw is not None else None
        except (TypeError, ValueError):
            left_s = None
        if status.get("paused"):
            return status
        # A prior Firebase debug attestation cooldown must not skip device ADB renew.
        if (not device_mode) and int(status.get("retry_after_seconds") or 0) > 0:
            return status
        needs_refresh = (
            not status.get("token_available")
            or left_s is None
            or left_s < floor
        )
        if not needs_refresh:
            return status
        if not (status.get("configured") or device.get("enabled")
                or status.get("configuration_present")):
            return status
        # Soft: reuse live session or ADB-read phone prefs. Hard: restart Minute when
        # the campaign floor is already breached and soft did not yield a token.
        self.ports.mint_app_check_token(force_refresh=False)
        status = self.ports.app_check_status()
        if status.get("token_available"):
            # A mirrored token below the device floor is not a renewal: escalate
            # instead of shipping a credential that expires mid-batch.
            left_now = self._available_token_left_s(status) if device_mode else None
            if not device_mode or left_now is None or left_now >= floor:
                return status
        self.ports.mint_app_check_token(force_refresh=True)
        return self.ports.app_check_status()

    def renew_app_check_for_batch(self) -> dict:
        """Renew App Check from the phone before a multi-account clip batch.

        Always re-reads SharedPreferences via ADB (ignores in-process session age).
        Escalates to a Minute restart when the live phone token is still unusable.
        Campaign/CLI callers often omit SOURCE; activate device defaults here unless
        the operator explicitly disabled capture.
        """
        try:
            roglife_appcheck = self.ports.device_provider()
            roglife_appcheck.ensure_device_defaults_for_writes()
        except Exception:
            pass
        status = self.ports.app_check_status()
        device = status.get("device") if isinstance(status.get("device"), dict) else {}
        if status.get("paused"):
            return status
        if not (status.get("configured") or device.get("enabled")
                or status.get("configuration_present")):
            return status
        try:
            floor = int(device.get("min_left_s") or 600)
        except (TypeError, ValueError):
            floor = 600
        floor = max(60, floor)
        self.ports.mint_app_check_token(force_refresh=False, bypass_live_session=True)
        status = self.ports.app_check_status()
        if status.get("token_available"):
            # Batch renewal needs the campaign floor, not the 30s transport margin.
            device_now = status.get("device") if isinstance(status.get("device"), dict) else {}
            left_now = self._available_token_left_s(status) if device_now.get("enabled") else None
            if not device_now.get("enabled") or left_now is None or left_now >= floor:
                return status
        self.ports.mint_app_check_token(force_refresh=True, bypass_live_session=True)
        return self.ports.app_check_status()
