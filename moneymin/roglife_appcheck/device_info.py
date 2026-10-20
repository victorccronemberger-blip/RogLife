"""Identidade e props reais do aparelho via adb (sem forja)."""
from __future__ import annotations

import re
import time

from .adb import adb_shell, read_android_id


def _prop(serial: str, name: str) -> str:
    result = adb_shell(serial, f"getprop {name}")
    return (result.stdout or "").strip()


def _uptime_ns(serial: str) -> int | None:
    """Aproxima SystemClock.elapsedRealtimeNanos a partir de /proc/uptime."""
    result = adb_shell(serial, "cat /proc/uptime")
    text = (result.stdout or "").strip().split()
    if not text:
        return None
    try:
        seconds = float(text[0])
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return int(seconds * 1_000_000_000)


def _minute_version(serial: str, package: str) -> dict[str, str]:
    result = adb_shell(serial, f"dumpsys package {package}", timeout=60)
    text = result.stdout or ""
    version_name = ""
    version_code = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("versionName="):
            version_name = line.split("=", 1)[1].strip()
        elif line.startswith("versionCode="):
            # versionCode=1004038 minSdk=24 targetSdk=36
            match = re.match(r"versionCode=(\d+)", line)
            if match:
                version_code = match.group(1)
        if version_name and version_code:
            break
    out = {}
    if version_name:
        out["version_name"] = version_name
    if version_code:
        out["version_code"] = version_code
    return out


def _network_type(serial: str) -> str | None:
    """Active transport as Minute ``network_type``: wifi | cellular.

    Reads ``dumpsys connectivity`` only. Returns None when the phone does not
    expose a usable active network (never invents wifi).
    """
    result = adb_shell(serial, "dumpsys connectivity", timeout=30)
    text = (result.stdout or "").casefold()
    if not text.strip():
        return None
    # Android 13+: the default network id plus the transport of its
    # NetworkAgentInfo block ("Active default network: 105" and
    # "... ni{WIFI CONNECTED ..."); older dumps fall back below.
    agents = dict(re.findall(
        r"networkagentinfo\{network\{(\d+)\}[^\n]*?ni\{(\w+)", text))
    active = {agents[net_id] for net_id in re.findall(
        r"active default network:\s*(\d+)", text) if net_id in agents}
    wifi_active = any("wifi" in transport for transport in active)
    cellular_active = any(
        "cellular" in transport or "mobile" in transport for transport in active)
    if wifi_active and not cellular_active:
        return "wifi"
    if cellular_active and not wifi_active:
        return "cellular"
    # Prefer explicit active NetworkAgent / NetworkInfo markers.
    wifi_hit = bool(re.search(
        r"(active\s+network[^\n]*(wifi|wlan)|networkinfo:.*type:\s*wifi|"
        r"type:\s*wifi,[^\n]*state:\s*connected|/wifi)",
        text))
    cellular_hit = bool(re.search(
        r"(active\s+network[^\n]*(mobile|cellular)|networkinfo:.*type:\s*mobile|"
        r"type:\s*mobile,[^\n]*state:\s*connected|/mobile)",
        text))
    if wifi_hit and not cellular_hit:
        return "wifi"
    if cellular_hit and not wifi_hit:
        return "cellular"
    if "transport=wifi" in text or "transport_wifi" in text:
        return "wifi"
    if "transport=cellular" in text or "transport_cellular" in text:
        return "cellular"
    return None


def _location_sample(serial: str) -> dict | None:
    """Last-known GPS sample from dumpsys location when present.

    Omits rather than forging coordinates. Shape matches the Minute header
    fields (lat/lng/accuracy/is_mock).
    """
    result = adb_shell(serial, "dumpsys location", timeout=30)
    text = result.stdout or ""
    if not text.strip():
        return None
    # Common last-location lines: "last location=Location[... lat=.. lng=.. ...]"
    match = re.search(
        r"last\s+location\s*=\s*Location\[[^\]]*?(-?\d+\.\d+)\s*,\s*(-?\d+\.\d+)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r"\blat(?:itude)?[=:\s]+(-?\d+\.\d+)[^\n]{0,80}?"
            r"l(?:ng|on)(?:gitude)?[=:\s]+(-?\d+\.\d+)",
            text,
            flags=re.IGNORECASE,
        )
    if not match:
        return None
    try:
        lat = float(match.group(1))
        lng = float(match.group(2))
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return None
    accuracy = 12.0
    acc_match = re.search(r"\bacc(?:uracy)?[=:\s]+(\d+(?:\.\d+)?)", text, flags=re.IGNORECASE)
    if acc_match:
        try:
            accuracy = float(acc_match.group(1))
        except (TypeError, ValueError):
            accuracy = 12.0
    if accuracy <= 0:
        accuracy = 12.0
    is_mock = bool(re.search(
        r"(isfrommockprovider\s*=\s*true|mock\s*=\s*true|\bisMock\s*=\s*true\b)",
        text,
        flags=re.IGNORECASE,
    ))
    return {
        "latitude": lat,
        "longitude": lng,
        "accuracy": accuracy,
        "is_mock": is_mock,
    }


def collect_device_info(serial: str, *, package: str = "com.bakerdata.minute") -> dict:
    """Lê Build.* / SSAID / uptime / versão / rede / GPS no aparelho físico."""
    device_id = read_android_id(serial)
    model = _prop(serial, "ro.product.model") or None
    manufacturer = _prop(serial, "ro.product.manufacturer") or None
    brand = _prop(serial, "ro.product.brand") or None
    device = _prop(serial, "ro.product.device") or None
    release = _prop(serial, "ro.build.version.release") or None
    sdk_raw = _prop(serial, "ro.build.version.sdk")
    try:
        sdk_int = int(sdk_raw) if sdk_raw else None
    except ValueError:
        sdk_int = None
    uptime_ns = _uptime_ns(serial)
    app = _minute_version(serial, package)
    network_type = _network_type(serial)
    location = _location_sample(serial)
    info = {
        "serial": serial,
        "device_id": device_id,
        "model": model,
        "manufacturer": manufacturer,
        "brand": brand,
        "product_device": device,
        "system_name": "Android",
        "system_version": release,
        "sdk_int": sdk_int,
        "uptime_ns": uptime_ns,
        "app_version": app.get("version_name"),
        "app_version_code": app.get("version_code"),
        "network_type": network_type,
        "location": location,
        "collected_at_unix": int(time.time()),
        "source": "adb_getprop",
    }
    # Remove vazios para não sobrescrever cache com nulls acidentais.
    return {key: value for key, value in info.items() if value not in (None, "", [])}
