"""Seleção de device e shell adb (lógica RogLife)."""
from __future__ import annotations

import subprocess


def list_adb_devices() -> list[dict]:
    result = subprocess.run(
        ["adb", "devices", "-l"], capture_output=True, text=True, timeout=15
    )
    devices = []
    for line in (result.stdout or "").splitlines():
        if not line.strip() or line.startswith("List"):
            continue
        parts = line.split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        serial = parts[0]
        meta = " ".join(parts[2:])
        devices.append({"serial": serial, "meta": meta, "line": line})
    return devices


def pick_device(cfg: dict, serial: str | None = None) -> str:
    devices = list_adb_devices()
    if not devices:
        raise RuntimeError("Nenhum device adb em estado 'device'")

    exclude = set(cfg.get("exclude_adb_serials") or [])
    if serial:
        if not any(item["serial"] == serial for item in devices):
            raise RuntimeError(f"Serial {serial} não está conectado")
        return serial

    physical = []
    for item in devices:
        candidate = item["serial"]
        if candidate in exclude:
            continue
        if candidate.startswith("127.0.0.1") or candidate.startswith("emulator-"):
            continue
        physical.append(item)

    prefer = cfg.get("prefer_physical_device", True)
    pool = physical if prefer and physical else devices
    if prefer and not physical:
        pool = devices
    return pool[0]["serial"]


def adb(serial: str, *args: str, timeout: int = 40) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["adb", "-s", serial, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def adb_shell(serial: str, cmd: str, timeout: int = 40) -> subprocess.CompletedProcess:
    return adb(serial, "shell", cmd, timeout=timeout)


def read_android_id(serial: str) -> str:
    result = adb_shell(serial, "settings get secure android_id")
    android_id = (result.stdout or "").strip()
    if android_id and android_id != "null":
        return f"android.ssaid:{android_id}"
    return "android.ssaid:unknown"
