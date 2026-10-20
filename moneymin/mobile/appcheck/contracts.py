"""Explicit state and late-bound operation ports for the App Check runtime."""
from __future__ import annotations

from pathlib import Path
from typing import Any, ContextManager, Protocol


class AppCheckState(Protocol):
    lock: Any
    identity: bytes | None
    token: str | None
    expires: float
    retry_at: float
    last_failure: str | None
    last_diagnostic: dict | None


class AppCheckPorts(Protocol):
    time: Any
    logger: Any

    def device_provider(self) -> Any: ...

    def _bearer_still_valid(self, token: str) -> bool:
        ...

    def _bearer_left_s(self, token: str) -> float | None:
        ...

    def _configured_bearer(self) -> str:
        ...

    def _exchange(self, project, app, api_key, secret) -> tuple[str, float]:
        ...

    def _exchange_file_lock(self) -> ContextManager[None]:
        ...

    def _is_paused(self) -> bool:
        ...

    def _load_persisted_state(self, digest: str) -> dict | None:
        ...

    def _restore_persisted_failure(self, digest: str, persisted: dict | None = None) -> None:
        ...

    def _save_persisted_state(
        self, digest: str, failure: str | None, cooldown_s: float, *,
        http_status: int | None = None, firebase_status: str | None = None,
    ) -> None:
        ...

    def _select_identity(self, values: tuple[str, ...], bearer: str) -> None:
        ...

    def _set_failure_diagnostic(
        self, category: str, *, http_status: int | None = None,
        firebase_status: str | None = None, retry_after_unix: float | None = None,
    ) -> None:
        ...

    def _settings(self) -> tuple[str, ...]:
        ...

    def _settings_digest(self, values: tuple[str, ...]) -> str:
        ...

    def _settings_identity(self, values: tuple[str, ...], bearer: str) -> bytes:
        ...

    def _state_path(self) -> Path:
        ...

    def app_check_status(self) -> dict:
        ...

    def last_mint_diagnostic(self) -> dict | None:
        ...

    def mint_app_check_token(
        self, force_refresh: bool = False, *, bypass_live_session: bool = False,
    ) -> str | None:
        ...
