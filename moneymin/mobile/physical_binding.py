"""Bind Minute wire device/auth fields to the connected physical phone.

Provider pipelines keep video (and measured IMU/frames of that same window).
CREATE/sidecar/header phone-describing fields — including calib/GOP sensor
signature — resolve from the current physical identity snapshot and change when
another connected device is selected.
"""
from __future__ import annotations

import json
import random
from functools import lru_cache
from pathlib import Path
from typing import Any

_PHYSICAL_CAPTURE_PATH = (
    Path(__file__).resolve().parent.parent / "resources" / "physical_capture_calib.json"
)


class PhysicalDeviceRequiredError(RuntimeError):
    """Device mode needs a usable connected-phone identity and did not get one."""

    def __init__(self, message: str, *, cause: str = "physical_identity_unavailable"):
        super().__init__(message)
        self.cause = cause


@lru_cache(maxsize=1)
def _load_physical_captures() -> dict[str, Any]:
    try:
        raw = json.loads(_PHYSICAL_CAPTURE_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        return {}
    captures = raw.get("captures") if isinstance(raw, dict) else None
    return dict(captures) if isinstance(captures, dict) else {}


def physical_capture_calib(model: str) -> dict[str, Any] | None:
    """Exact Minute-native calib for a Build.MODEL, when a real capture exists."""
    if not (isinstance(model, str) and model.strip()):
        return None
    entry = _load_physical_captures().get(model.strip())
    if not isinstance(entry, dict):
        return None
    if not isinstance(entry.get("distortion_model"), str) or not entry["distortion_model"]:
        return None
    if not isinstance(entry.get("cameraName"), str) or not entry["cameraName"].strip():
        return None
    out = dict(entry)
    out["cameraName"] = str(entry["cameraName"]).strip()
    out["logicalCameraId"] = str(entry.get("logicalCameraId") or "2")
    return out


def _validated_identity(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PhysicalDeviceRequiredError(
            "Identidade física do celular ausente ou inválida.",
            cause="physical_identity_unavailable",
        )
    device_id = raw.get("device_id")
    model = raw.get("model")
    app_version = raw.get("app_version")
    sdk = raw.get("sdk_int")
    uptime = raw.get("uptime_ns")
    if not (isinstance(device_id, str) and device_id.startswith("android.ssaid:")
            and device_id[len("android.ssaid:"):].strip()):
        raise PhysicalDeviceRequiredError(
            "Celular conectado sem device_id SSAID utilizável.",
            cause="physical_device_id_missing",
        )
    if not (isinstance(model, str) and model.strip()):
        raise PhysicalDeviceRequiredError(
            "Celular conectado sem Build.MODEL utilizável.",
            cause="physical_model_missing",
        )
    if not (isinstance(app_version, str) and app_version.strip()):
        raise PhysicalDeviceRequiredError(
            "Celular conectado sem appVersion do Minute utilizável.",
            cause="physical_app_version_missing",
        )
    if type(sdk) is not int or sdk <= 0:
        raise PhysicalDeviceRequiredError(
            "Celular conectado sem sdk_int utilizável.",
            cause="physical_sdk_missing",
        )
    if type(uptime) is not int or uptime <= 0:
        raise PhysicalDeviceRequiredError(
            "Celular conectado sem uptime_ns utilizável.",
            cause="physical_uptime_missing",
        )
    out = dict(raw)
    out["device_id"] = device_id.strip()
    out["model"] = model.strip()
    out["app_version"] = app_version.strip()
    return out


def _sensor_signature(model: str, device_id: str) -> tuple[dict[str, Any], int, float]:
    """Calib UW + GOP/bitrate for this physical SSAID/model (not per-account).

    Prefer an exact Minute-native capture when one was ported for Build.MODEL
    (today: SM-S901E from the sole known real upload). Otherwise fall back to
    the catalog reference with a SSAID-stable jitter.
    """
    from .. import device_catalog

    rng = random.Random(f"moneymin.physical.sensor:{device_id}")
    capture = physical_capture_calib(model)
    if capture is not None:
        calib = dict(capture)
    else:
        calib = device_catalog.calibration_for_model(model, rng)
    if not isinstance(calib, dict) or not isinstance(calib.get("distortion_model"), str):
        raise PhysicalDeviceRequiredError(
            "Celular conectado sem calibração utilizável para o Build.MODEL.",
            cause="physical_calib_unavailable",
        )
    gop, bitrate = device_catalog.video_metrics(rng)
    gop = int(gop)
    bitrate = float(bitrate)
    if gop <= 0:
        raise PhysicalDeviceRequiredError(
            "Celular conectado sem frames_gop utilizável.",
            cause="physical_gop_unavailable",
        )
    if bitrate <= 0:
        raise PhysicalDeviceRequiredError(
            "Celular conectado sem video_bitrate utilizável.",
            cause="physical_bitrate_unavailable",
        )
    return calib, gop, bitrate


def _location_header(snap: dict[str, Any]) -> str | None:
    """Optional Minute ``X-Device-Location`` CSV from a phone sample only."""
    location = snap.get("location")
    if not isinstance(location, dict):
        return None
    try:
        lat = float(location["latitude"])
        lng = float(location["longitude"])
        accuracy = float(location.get("accuracy") or 12.0)
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return None
    if accuracy <= 0 or accuracy != accuracy:  # NaN guard
        accuracy = 12.0
    is_mock = "true" if location.get("is_mock") is True else "false"
    return f"{lat:.6f},{lng:.6f},{round(accuracy)},{is_mock}"


def _optional_network_fields(snap: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Phone network_type sample when present (wifi|cellular)."""
    network_type = snap.get("network_type")
    if not (isinstance(network_type, str) and network_type.strip().casefold() in {"wifi", "cellular"}):
        return None, None
    network_type = network_type.strip().casefold()
    carrier = snap.get("network_carrier")
    if carrier is not None and not (isinstance(carrier, str) and carrier.strip()):
        carrier = None
    elif isinstance(carrier, str):
        carrier = carrier.strip()
    return network_type, {"type": network_type, "carrier": carrier}


def require_physical_network(bound: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Refuse when device mode needs network_type and the phone sample is absent."""
    network_type = bound.get("network_type")
    network_meta = bound.get("network_meta")
    if not (isinstance(network_type, str) and network_type in {"wifi", "cellular"}
            and isinstance(network_meta, dict) and network_meta.get("type") == network_type):
        raise PhysicalDeviceRequiredError(
            "Celular conectado sem network_type utilizável (wifi|cellular).",
            cause="physical_network_unavailable",
        )
    return network_type, network_meta


def physical_wire_fields(
        identity: dict[str, Any], *, wall_ms: int | None = None) -> dict[str, Any]:
    """Pure: identity snapshot → CREATE/sidecar/header-bound phone fields."""
    from .. import config as app_config

    snap = _validated_identity(identity)
    model = snap["model"]
    device_id = snap["device_id"]
    system_name = snap.get("system_name")
    if not (isinstance(system_name, str) and system_name.strip()):
        system_name = "Android"
    else:
        system_name = system_name.strip()
    system_version = snap.get("system_version")
    sidecar_device = {
        "model": model,
        "systemName": system_name,
    }
    if isinstance(system_version, str) and system_version.strip():
        sidecar_device["systemVersion"] = system_version.strip()
    uptime_ns = int(snap["uptime_ns"])
    collected = snap.get("collected_at_unix")
    if wall_ms is not None and type(collected) in (int, float) and collected > 0:
        delta_ms = int(wall_ms) - int(float(collected) * 1000)
        uptime_ns = max(1, uptime_ns + delta_ms * 1_000_000)
    injected_calib = snap.get("calib")
    injected_gop = snap.get("frames_gop")
    injected_br = snap.get("video_bitrate_mbps")
    derived_calib, derived_gop, derived_br = _sensor_signature(model, device_id)
    if isinstance(injected_calib, dict) and isinstance(
            injected_calib.get("distortion_model"), str) and injected_calib["distortion_model"]:
        calib = dict(injected_calib)
    else:
        calib = derived_calib
    if type(injected_gop) is int and injected_gop > 0:
        frames_gop = injected_gop
    else:
        frames_gop = derived_gop
    if type(injected_br) in (int, float) and float(injected_br) > 0:
        video_bitrate_mbps = float(injected_br)
    else:
        video_bitrate_mbps = derived_br
    network_type, network_meta = _optional_network_fields(snap)
    return {
        "device_id": device_id,
        "serial": snap.get("serial") if isinstance(snap.get("serial"), str) else None,
        "model": model,
        "app_version": snap["app_version"],
        "upload_device": {"model": model},
        "upload_platform": {"os": "android"},
        "sidecar_device": sidecar_device,
        "sidecar_platform": {"os": "android", "version": int(snap["sdk_int"])},
        "uptime_ns": uptime_ns,
        "calib": calib,
        "frames_gop": frames_gop,
        "video_bitrate_mbps": video_bitrate_mbps,
        # Residuals: OkHttp UA is device-agnostic; opened/network/location follow phone.
        "user_agent": str(app_config.USER_AGENT),
        "opened": {
            "app_version": snap["app_version"],
            "device_model": model,
            "os_version": f"android {int(snap['sdk_int'])}",
        },
        "network_type": network_type,
        "network_meta": network_meta,
        "location_header": _location_header(snap),
        "identity": snap,
    }


def current_physical_identity(*, identity: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the injected snapshot or the live device-mode physical identity."""
    if identity is not None:
        return _validated_identity(identity)
    from ..roglife_appcheck import provider as roglife

    if not roglife.device_mode_enabled():
        raise PhysicalDeviceRequiredError(
            "Modo device desligado; identidade física do celular é obrigatória neste caminho.",
            cause="device_mode_disabled",
        )
    snap = roglife.physical_identity()
    if not snap:
        raise PhysicalDeviceRequiredError(
            "Nenhum celular físico conectado com identidade utilizável "
            "(device_id/model/app_version/sdk/uptime).",
            cause="physical_identity_unavailable",
        )
    return _validated_identity(snap)


def require_campaign_physical_binding(
        *,
        identity: dict[str, Any] | None = None,
        wall_ms: int | None = None,
) -> dict[str, Any]:
    """Resolve current (or injected) physical identity into wire fields."""
    return physical_wire_fields(
        current_physical_identity(identity=identity), wall_ms=wall_ms)


def device_mode_requires_physical() -> bool:
    from ..roglife_appcheck import provider as roglife

    return bool(roglife.device_mode_enabled())
