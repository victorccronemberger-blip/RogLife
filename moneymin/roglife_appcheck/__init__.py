"""App Check Play Integrity + identidade física do aparelho (origem RogLife)."""
from __future__ import annotations

from .provider import (
    cache_path,
    clear_live_token,
    device_mode_enabled,
    device_mode_explicitly_disabled,
    ensure_device_defaults_for_writes,
    obtain_token,
    physical_app_version,
    physical_device_id,
    physical_identity,
    physical_sidecar_device_meta,
    physical_sidecar_platform_meta,
    physical_upload_device_meta,
    physical_upload_platform_meta,
    physical_uptime_ns,
    read_cached_token,
    refresh_device_info,
    status_snapshot,
)

__all__ = [
    "cache_path",
    "clear_live_token",
    "device_mode_enabled",
    "device_mode_explicitly_disabled",
    "ensure_device_defaults_for_writes",
    "obtain_token",
    "physical_app_version",
    "physical_device_id",
    "physical_identity",
    "physical_sidecar_device_meta",
    "physical_sidecar_platform_meta",
    "physical_upload_device_meta",
    "physical_upload_platform_meta",
    "physical_uptime_ns",
    "read_cached_token",
    "refresh_device_info",
    "status_snapshot",
]
