"""Public, installed Minute version; no token capture or disk authority.

ADB/config hooks are resolved on their existing modules at call time. Inventory
is checked even on cache hits: only authorized ``device`` entries returned by
the legacy inventory API are eligible. Its optional physical preference and
exclusions apply before automatic selection; an ambiguous pool is never picked
arbitrarily. Explicit selection is QMONEY_APPCHECK_ADB_SERIAL, then ANDROID_SERIAL.

Observations (including unknown versions) are diagnostic, not remote acceptance.
Call ``client_version(force_refresh=True, require_reviewed=True)`` at a write /
campaign admission boundary, not as a blanket restriction on diagnostic reads.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import os
import re
import subprocess
import threading
import time

from .. import config
from ..roglife_appcheck import adb
from ..roglife_appcheck import config as roglife_config

__all__ = [
    "MinuteVersionError", "observe_installed_version", "require_reviewed_version",
    "client_version", "clear_observation_cache",
]

_TTL_SECONDS = 12.0
_ADB_TIMEOUT_SECONDS = 15
_MINUTE_PACKAGE = "com.bakerdata.minute"
# Reviewed locally, NOT certified compatible or accepted by the backend.
# Evidence/provenance: AUDITORIA_MINUTE_1_30.md (local review limits).
# Quota/metadata contracts were locally equivalent; App Check/retry changed.
_REVIEWED_PAIRS = frozenset({("1.29.0", 1004038), ("1.30.0", 1004039)})
_PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")
_SERIAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-\[\]]{0,255}")
_PRE_ID = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
_SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    rf"(?:-{_PRE_ID}(?:\.{_PRE_ID})*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
_CODE_LINE = re.compile(r"([1-9][0-9]{0,18})(?:[ \t]+(?:minSdk|targetSdk)=[0-9]+)*")
_MESSAGES = {
    "configuration_unavailable": "Configuração móvel indisponível ou inválida.",
    "invalid_package": "Pacote Android ausente ou inválido na configuração móvel.",
    "invalid_selection": "Seleção do dispositivo Android inválida.",
    "device_unavailable": "Dispositivo Android selecionado ausente ou não autorizado.",
    "device_ambiguous": "Mais de um dispositivo Android elegível; selecione um explicitamente.",
    "adb_unavailable": "ADB indisponível para observar a versão instalada.",
    "adb_timeout": "Prazo excedido ao observar a versão instalada pelo ADB.",
    "adb_failed": "ADB não confirmou a consulta pública da versão instalada.",
    "invalid_observation": "Pacote instalado sem uma observação de versão única e válida.",
    "observation_reset": "Observação móvel invalidada; faça uma nova consulta.",
    "unreviewed_version": (
        "Versão/build instalado ainda não revisado para escrita. "
        "Atualize e revise a integração antes de iniciar a campanha; "
        "alterar apenas o número da versão não é suficiente."
    ),
}


class MinuteVersionError(RuntimeError):
    """Fixed, sanitized message and stable cause; never includes transport data."""

    def __init__(self, cause: str = "invalid_observation"):
        self.cause = cause if isinstance(cause, str) and cause in _MESSAGES else "invalid_observation"
        super().__init__(_MESSAGES[self.cause])


@dataclass
class _Flight:
    generation: int
    done: threading.Event = field(default_factory=threading.Event)
    snapshot: dict | None = None
    cause: str | None = None
    refreshed: bool = False


_lock = threading.RLock()
_cache: dict[tuple, dict] = {}
_flights: dict[tuple, _Flight] = {}
_generation = 0


def clear_observation_cache() -> None:
    """Forget only this module's memory; old in-flight reads cannot repopulate it."""
    global _generation
    with _lock:
        _generation += 1
        _cache.clear()
        _flights.clear()


def _context() -> tuple[dict, str | None, tuple]:
    try:
        raw = roglife_config.load_config()
        if not isinstance(raw, dict):
            raise MinuteVersionError("configuration_unavailable")
        package = raw.get("package")
        if not (isinstance(package, str) and len(package) <= 255
                and _PACKAGE.fullmatch(package)):
            raise MinuteVersionError("invalid_package")
        exclusions = raw.get("exclude_adb_serials", [])
        prefer = raw.get("prefer_physical_device", True)
        if (not isinstance(exclusions, (list, tuple)) or type(prefer) is not bool
                or any(not isinstance(s, str) or not _SERIAL.fullmatch(s) for s in exclusions)):
            raise MinuteVersionError("configuration_unavailable")
        explicit = os.environ.get("QMONEY_APPCHECK_ADB_SERIAL", "").strip()
        android = os.environ.get("ANDROID_SERIAL", "").strip()
        chosen = explicit or android or None
        if chosen is not None and not _SERIAL.fullmatch(chosen):
            raise MinuteVersionError("invalid_selection")
        # Only public selection settings enter the cache key, never full config
        # (which may contain unrelated App Check settings). No cache file is read.
        key = (
            str(config.ROOT), str(config.DATA_DIR),
            os.environ.get("QMONEY_USER_ROOT", ""), os.environ.get("LOCALAPPDATA", ""),
            str(roglife_config.config_path()), explicit, android, package,
            prefer, tuple(sorted(set(exclusions))),
        )
        cfg = {"package": package, "prefer_physical_device": prefer,
               "exclude_adb_serials": list(exclusions)}
        return cfg, chosen, key
    except MinuteVersionError:
        raise
    except Exception:
        raise MinuteVersionError("configuration_unavailable") from None


def _select_device(cfg: dict, chosen: str | None) -> str:
    # list_adb_devices already filters to authorized state=device. Accept an
    # explicit state from replacement APIs too, but never authorize other states.
    devices = adb.list_adb_devices()
    if not isinstance(devices, list):
        raise MinuteVersionError("adb_failed")
    eligible = []
    seen = set()
    for item in devices:
        if not isinstance(item, dict):
            raise MinuteVersionError("adb_failed")
        if item.get("state", "device") != "device":
            continue
        serial = item.get("serial")
        if not isinstance(serial, str) or not _SERIAL.fullmatch(serial):
            raise MinuteVersionError("invalid_selection")
        if serial in seen:
            raise MinuteVersionError("device_ambiguous")
        seen.add(serial)
        if serial not in cfg["exclude_adb_serials"]:
            eligible.append(serial)
    if chosen is not None:
        if chosen not in eligible:
            raise MinuteVersionError("device_unavailable")
    else:
        physical = [s for s in eligible if not s.startswith(("127.0.0.1", "emulator-"))]
        pool = physical if cfg["prefer_physical_device"] and physical else eligible
        if not pool:
            raise MinuteVersionError("device_unavailable")
        if len(pool) != 1:
            raise MinuteVersionError("device_ambiguous")
        chosen = pool[0]
    try:
        picked = adb.pick_device(cfg, chosen)
    except (subprocess.TimeoutExpired, TimeoutError, OSError):
        raise
    except Exception:
        raise MinuteVersionError("device_unavailable") from None
    if picked != chosen:
        raise MinuteVersionError("invalid_selection")
    return chosen


def _valid_name(value: object) -> bool:
    return isinstance(value, str) and len(value) <= 128 and _SEMVER.fullmatch(value) is not None


def _parse_installed(text: object, package: str) -> dict:
    if not isinstance(text, str) or not text or len(text) > 2_000_000:
        raise MinuteVersionError("invalid_observation")
    # Never accept a stray versionName or an APK/path's metadata. Exactly one
    # Package block must identify the configured installed package.
    lines = text.splitlines()
    headers = []
    for i, line in enumerate(lines):
        match = re.fullmatch(r"([ \t]*)Package \[([^\]\r\n]+)\](?: \([^\r\n)]*\))?:[ \t]*", line)
        if match:
            headers.append((i, len(match[1]), match[2]))
    if len(headers) != 1 or headers[0][2] != package:
        raise MinuteVersionError("invalid_observation")
    start, indent, _ = headers[0]
    fields: dict[str, str] = {}
    installed = False
    for line in lines[start + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        if re.match(r"\s*User [0-9]+:", line) and re.search(r"\binstalled=true\b", line):
            installed = True
        match = re.fullmatch(r"\s*(versionName|versionCode|lastUpdateTime)=(.*)", line)
        if match:
            if match[1] in fields:
                raise MinuteVersionError("invalid_observation")
            fields[match[1]] = match[2]
    name = fields.get("versionName")
    code_match = _CODE_LINE.fullmatch(fields.get("versionCode", ""))
    if not installed or not _valid_name(name) or code_match is None:
        raise MinuteVersionError("invalid_observation")
    out = {"version_name": name, "version_code": int(code_match[1])}
    if "lastUpdateTime" in fields:
        updated = fields["lastUpdateTime"]
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?", updated):
            raise MinuteVersionError("invalid_observation")
        try:
            datetime.strptime(updated[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise MinuteVersionError("invalid_observation") from None
        out["last_update_time"] = updated
    return out


def _read(cfg: dict, chosen: str | None, key: tuple, force: bool) -> tuple[dict, bool]:
    serial = _select_device(cfg, chosen)
    with _lock:
        cached = _cache.get(key + (serial,))
        if not force and cached is not None:
            age = time.monotonic() - cached["observed_at_monotonic"]
            if 0 <= age < _TTL_SECONDS:
                return dict(cached), False
    result = adb.adb_shell(serial, f"dumpsys package {cfg['package']}", timeout=_ADB_TIMEOUT_SECONDS)
    if type(result.returncode) is not int or result.returncode != 0:
        raise MinuteVersionError("adb_failed")
    snapshot = _parse_installed(result.stdout, cfg["package"])
    snapshot.update(serial=serial, package=cfg["package"], observed_at_monotonic=time.monotonic())
    return snapshot, True


def _transport_cause(error: Exception) -> str:
    if isinstance(error, MinuteVersionError):
        return error.cause
    if isinstance(error, (subprocess.TimeoutExpired, TimeoutError)):
        return "adb_timeout"
    if isinstance(error, FileNotFoundError):
        return "adb_unavailable"
    return "adb_failed"


def observe_installed_version(*, force_refresh: bool = False) -> dict:
    """Observe public package metadata; version_code is int, timestamp monotonic.

    Cache TTL is 12s, keyed by profile/config/selection/package AND actual serial.
    Every call revalidates inventory, even a cache hit. Concurrent readers share
    one flight per context. A forced reader may share an in-flight live read,
    never an old cache hit. Errors evict that context instead of serving stale.
    """
    try:
        cfg, chosen, key = _context()
    except MinuteVersionError:
        clear_observation_cache()
        raise
    while True:
        with _lock:
            flight = _flights.get(key)
            leader = flight is None
            if leader:
                flight = _Flight(_generation)
                _flights[key] = flight
        if leader:
            snapshot, refreshed, cause = None, False, None
            try:
                snapshot, refreshed = _read(cfg, chosen, key, force_refresh)
            except Exception as error:
                cause = _transport_cause(error)
            finally:
                # Also release waiters on cancellation/BaseException; no stale
                # entry survives an interrupted flight.
                with _lock:
                    reset = flight.generation != _generation
                    if reset:
                        cause = "observation_reset"
                    if cause is not None or snapshot is None:
                        cause = cause or "adb_failed"
                        if not reset:
                            for entry in list(_cache):
                                if entry[:-1] == key:
                                    del _cache[entry]
                    elif refreshed:
                        _cache[key + (snapshot["serial"],)] = dict(snapshot)
                    flight.snapshot, flight.refreshed, flight.cause = snapshot, refreshed, cause
                    if _flights.get(key) is flight:
                        del _flights[key]
                    flight.done.set()
        else:
            flight.done.wait()
        if flight.cause is not None:
            raise MinuteVersionError(flight.cause) from None
        if force_refresh and not flight.refreshed:
            continue
        return dict(flight.snapshot)


def require_reviewed_version(snapshot: dict) -> str:
    """Local write/admission gate, not a claim of complete/remote compatibility."""
    if not isinstance(snapshot, dict):
        raise MinuteVersionError("invalid_observation")
    name, code = snapshot.get("version_name"), snapshot.get("version_code")
    if not _valid_name(name) or type(code) is not int or code <= 0:
        raise MinuteVersionError("invalid_observation")
    if snapshot.get("package") != _MINUTE_PACKAGE or (name, code) not in _REVIEWED_PAIRS:
        raise MinuteVersionError("unreviewed_version")
    return name


def client_version(*, force_refresh: bool = False, require_reviewed: bool = False) -> str:
    """Use the live installed source in device mode; otherwise config.APP_VERSION.

    No remote/env version override, activation of device mode, or fallback from
    a missing/invalid device observation to a hard-coded version is performed.
    """
    from ..roglife_appcheck import provider

    if not provider.device_mode_enabled():
        return config.APP_VERSION
    snapshot = observe_installed_version(force_refresh=force_refresh)
    return require_reviewed_version(snapshot) if require_reviewed else snapshot["version_name"]
