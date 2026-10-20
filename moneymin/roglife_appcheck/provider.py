"""Provider de App Check + identidade física do S22 para o Finally."""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from .adb import pick_device
from .capture import ensure_appcheck
from .config import load_config
from .device_info import collect_device_info

logger = logging.getLogger(__name__)
_lock = threading.RLock()

_SOURCE_ENV = "QMONEY_APPCHECK_SOURCE"
_DEVICE_FLAG_ENV = "QMONEY_APPCHECK_DEVICE"
_SERIAL_ENV = "QMONEY_APPCHECK_ADB_SERIAL"

# Last JWT obtained by live ADB from the phone in this process. Disk files never
# authorize App Check; status validity comes only from this session.
_live: dict | None = None

_TOKEN_CACHE_KEYS = (
    "token", "iat", "exp", "provider", "left", "obtained_at_iso",
    "method", "source", "valido_ate_iso",
    # Status-only fields must never be persisted as if they authorized a JWT.
    "token_source", "token_available", "token_len", "left_s",
)


def device_mode_explicitly_disabled() -> bool:
    """True only when SOURCE/DEVICE env forbids Play Integrity device capture."""
    source = os.environ.get(_SOURCE_ENV, "").strip().casefold()
    if source in {"off", "0", "false", "no", "bearer", "debug", "none"}:
        return True
    flag = os.environ.get(_DEVICE_FLAG_ENV, "").strip().casefold()
    return flag in {"0", "false", "no", "off"}


def device_mode_enabled() -> bool:
    """Play Integrity device App Check is on for local desktop edition by default.

    Explicit SOURCE/DEVICE env values still win. SOURCE=off or DEVICE=0 disables
    even when QMONEY_LOCAL_EDITION is set by the Qt embedded service.
    """
    source = os.environ.get(_SOURCE_ENV, "").strip().casefold()
    if source in {"device", "physical", "roglife", "play_integrity"}:
        return True
    if device_mode_explicitly_disabled():
        return False
    flag = os.environ.get(_DEVICE_FLAG_ENV, "").strip().casefold()
    if flag in {"1", "true", "yes", "on"}:
        return True
    local = os.environ.get("QMONEY_LOCAL_EDITION", "").strip().casefold()
    return local in {"1", "true", "yes", "on"}


def ensure_device_defaults_for_writes() -> dict:
    """Activate device App Check defaults for campaign/write paths.

    Does not override an explicit off/bearer/debug choice. When SOURCE/DEVICE
    are unset (typical bare CLI), sets SOURCE=device. When ADB serial is unset,
    prefers the last known physical serial from the identity cache, then
    ANDROID_SERIAL. Returns a small activation report for diagnostics.
    """
    report = {
        "enabled": False,
        "activated_source": False,
        "activated_serial": False,
        "explicitly_disabled": device_mode_explicitly_disabled(),
        "serial": None,
    }
    if report["explicitly_disabled"]:
        return report

    # Empty SOURCE counts as unset (setdefault would keep "").
    if not device_mode_enabled():
        os.environ[_SOURCE_ENV] = "device"
        report["activated_source"] = True

    if not os.environ.get(_SERIAL_ENV, "").strip():
        serial = None
        raw = _read_cache_file() or {}
        cached = raw.get("serial")
        if isinstance(cached, str) and cached.strip():
            serial = cached.strip()
        if not serial:
            android = os.environ.get("ANDROID_SERIAL", "").strip()
            if android:
                serial = android
        if serial:
            os.environ[_SERIAL_ENV] = serial
            report["activated_serial"] = True

    report["enabled"] = device_mode_enabled()
    report["serial"] = os.environ.get(_SERIAL_ENV, "").strip() or None
    return report


def cache_path() -> Path:
    # Identity props may still be stored for CREATE headers. JWT authorization
    # never reads this file. Explicit profiles keep precedence for tests.
    explicit_root = os.environ.get("QMONEY_USER_ROOT", "").strip()
    if explicit_root:
        root = Path(explicit_root).expanduser().resolve()
        return root / "data" / "appcheck_device.json"
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if sys.platform == "win32" and local_app_data:
        return Path(local_app_data) / "QMoney" / "data" / "appcheck_device.json"
    return Path(config.DATA_DIR) / "appcheck_device.json"


def _jwt_left_s(token: str) -> int | None:
    try:
        import base64

        parts = token.split(".")
        if len(parts) != 3:
            return None
        pad = "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
        exp = payload.get("exp")
        if type(exp) not in (int, float):
            return None
        return int(exp) - int(time.time())
    except (ValueError, TypeError, json.JSONDecodeError, OSError):
        return None


def _read_cache_file() -> dict | None:
    path = cache_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _identity_only(raw: dict) -> dict:
    """Persist device props without a JWT that could be mistaken for authority."""
    out = {}
    for key, value in raw.items():
        if key in _TOKEN_CACHE_KEYS:
            continue
        out[key] = value
    return out


def read_cached_token(*, min_left_s: int = 30) -> dict | None:
    """Return the in-process live ADB session token when still above the floor.

    Never reads JWT from disk. Name kept for callers; source is live session only.
    """
    with _lock:
        raw = dict(_live) if _live else None
    if not raw:
        return None
    token = raw.get("token")
    if not isinstance(token, str) or not token:
        return None
    left = _jwt_left_s(token)
    if left is None or left < min_left_s:
        return None
    out = dict(raw)
    out["left"] = left
    out["token"] = token
    return out


def clear_live_token() -> None:
    """Drop the process live JWT (tests and explicit operator reset)."""
    global _live
    with _lock:
        _live = None


def physical_identity() -> dict | None:
    """Snapshot físico (device_id, model, sdk, app_version, uptime…)."""
    if not device_mode_enabled():
        return None
    raw = _read_cache_file()
    if not raw:
        return None
    device_id = raw.get("device_id")
    if not (isinstance(device_id, str) and device_id.startswith("android.ssaid:")
            and device_id[len("android.ssaid:"):].strip()):
        return None
    return _identity_only(raw)


def physical_device_id() -> str | None:
    identity = physical_identity() or {}
    device_id = identity.get("device_id")
    if (isinstance(device_id, str) and device_id.startswith("android.ssaid:")
            and device_id[len("android.ssaid:"):].strip()):
        return device_id
    return None


def physical_upload_device_meta() -> dict[str, str] | None:
    """POST curto: apenas Build.MODEL do aparelho."""
    identity = physical_identity() or {}
    model = identity.get("model")
    if isinstance(model, str) and model.strip():
        return {"model": model.strip()}
    return None


def physical_upload_platform_meta() -> dict[str, str] | None:
    """POST curto: apenas os=android (como o app)."""
    if not device_mode_enabled() or not physical_identity():
        return None
    return {"os": "android"}


def physical_sidecar_device_meta() -> dict[str, str] | None:
    """metadata.json completo: model + systemName + systemVersion do telefone."""
    identity = physical_identity() or {}
    model = identity.get("model")
    system_version = identity.get("system_version")
    if not (isinstance(model, str) and model.strip()):
        return None
    meta = {
        "model": model.strip(),
        "systemName": str(identity.get("system_name") or "Android"),
    }
    if isinstance(system_version, str) and system_version.strip():
        meta["systemVersion"] = system_version.strip()
    return meta


def physical_sidecar_platform_meta() -> dict[str, object] | None:
    """metadata.json: {os, version: sdkInt} lido do telefone."""
    identity = physical_identity() or {}
    sdk = identity.get("sdk_int")
    if type(sdk) is not int:
        return None
    return {"os": "android", "version": sdk}


def physical_app_version() -> str | None:
    identity = physical_identity() or {}
    version = identity.get("app_version")
    if isinstance(version, str) and version.strip():
        return version.strip()
    return None


def physical_uptime_ns() -> int | None:
    identity = physical_identity() or {}
    value = identity.get("uptime_ns")
    if type(value) is int and value > 0:
        return value
    return None


def _write_cache(payload: dict) -> Path:
    """Persist identity props only; JWT/status authority fields are stripped."""
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_identity_only(payload), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _merge_device_fields(base: dict, info: dict) -> dict:
    out = dict(base)
    for key in (
        "serial", "device_id", "model", "manufacturer", "brand", "product_device",
        "system_name", "system_version", "sdk_int", "uptime_ns",
        "app_version", "app_version_code", "collected_at_unix",
        "network_type", "location",
    ):
        if key in info:
            out[key] = info[key]
    out["device_info_source"] = info.get("source", "adb_getprop")
    return out


def refresh_device_info(*, serial: str | None = None) -> dict:
    """Atualiza props do telefone no disco (barato; não renova App Check)."""
    if not device_mode_enabled():
        raise RuntimeError("QMONEY_APPCHECK_SOURCE=device é necessário")
    with _lock:
        cfg = load_config()
        chosen = serial or os.environ.get(_SERIAL_ENV, "").strip() or None
        device = pick_device(cfg, chosen)
        info = collect_device_info(device, package=str(cfg.get("package") or "com.bakerdata.minute"))
        raw = _identity_only(_read_cache_file() or {})
        merged = _merge_device_fields(raw, info)
        _write_cache(_identity_only(merged))
        return info


def status_snapshot() -> dict:
    """Estado do provider: identidade pode vir do disco; JWT só da sessão ADB ao vivo."""
    enabled = device_mode_enabled()
    identity = _identity_only(_read_cache_file() or {})
    try:
        min_left = int(load_config().get("min_appcheck_left_s", 600))
    except (OSError, ValueError, FileNotFoundError):
        min_left = 600

    with _lock:
        live = dict(_live) if _live else None
    token = live.get("token") if live and isinstance(live.get("token"), str) else None
    left = _jwt_left_s(token) if token else None
    token_available = bool(token and left is not None and left >= min_left)
    provider_name = (live or {}).get("provider")
    serial = identity.get("serial") or (live or {}).get("serial")
    device_id = identity.get("device_id") or (live or {}).get("device_id") or physical_device_id()
    return {
        "enabled": enabled,
        "source": "device" if enabled else "off",
        "cache_path": str(cache_path()),
        "token_available": token_available,
        "token_source": "adb_live" if token else None,
        "provider": provider_name,
        "left_s": left,
        "min_left_s": min_left,
        "serial": serial,
        "device_id": device_id,
        "model": identity.get("model") or (live or {}).get("model"),
        "system_version": identity.get("system_version") or (live or {}).get("system_version"),
        "sdk_int": identity.get("sdk_int") if identity.get("sdk_int") is not None
        else (live or {}).get("sdk_int"),
        "app_version": identity.get("app_version") or (live or {}).get("app_version"),
        "uptime_ns": identity.get("uptime_ns") if identity.get("uptime_ns") is not None
        else (live or {}).get("uptime_ns"),
        "token_len": len(token) if token else 0,
    }


def obtain_token(
    *,
    force: bool = False,
    serial: str | None = None,
    bypass_live_session: bool = False,
) -> str | None:
    """JWT Play Integrity via ADB; disco nunca autoriza.

    Sem ``force`` e sem ``bypass_live_session``, reutiliza a sessão ADB deste
    processo enquanto estiver acima do piso (~10 min). Lotes de campanha passam
    ``bypass_live_session=True`` para reler o telefone a cada lote. ``force``
    reinicia o Minute quando as prefs do aparelho estão curtas/ausentes.
    """
    global _live
    if not device_mode_enabled():
        return None

    with _lock:
        cfg = load_config()
        min_left = int(cfg.get("min_appcheck_left_s", 600))
        package = str(cfg.get("package") or "com.bakerdata.minute")
        chosen = serial or os.environ.get(_SERIAL_ENV, "").strip() or None

        if not force and not bypass_live_session:
            cached = read_cached_token(min_left_s=min_left)
            if cached and isinstance(cached.get("token"), str):
                os.environ["FIREBASE_APP_CHECK_BEARER"] = cached["token"]
                return cached["token"]

        if not chosen:
            # Identity cache / ANDROID_SERIAL already applied by write defaults;
            # still prefer a cached physical serial when callers skip defaults.
            raw = _read_cache_file() or {}
            cached_serial = raw.get("serial")
            if isinstance(cached_serial, str) and cached_serial.strip():
                chosen = cached_serial.strip()

        device = pick_device(cfg, chosen)
        # Props do telefone: sempre atualiza (uptime muda; model/sdk são baratos).
        info = collect_device_info(device, package=package)

        # Streaming: lê/renova SharedPreferences no telefone. force reinicia o Minute.
        token_info = ensure_appcheck(device, cfg, force=force)
        live_payload = _merge_device_fields(
            {
                "token": token_info["token"],
                "iat": token_info.get("iat"),
                "exp": token_info.get("exp"),
                "provider": token_info.get("provider"),
                "left": token_info.get("left"),
                "obtained_at_iso": datetime.now(timezone.utc).isoformat(),
                "method": "adb-su-shared_prefs",
                "source": "roglife_appcheck",
                "token_source": "adb_live",
            },
            info,
        )
        if token_info.get("exp"):
            live_payload["valido_ate_iso"] = datetime.fromtimestamp(
                token_info["exp"], tz=timezone.utc
            ).isoformat()
        _live = dict(live_payload)
        # Disco guarda só identidade — JWT morto em arquivo não aparece como Expirado.
        _write_cache(_identity_only(live_payload))
        os.environ["FIREBASE_APP_CHECK_BEARER"] = token_info["token"]
        logger.info(
            "App Check ao vivo via ADB (provider=%s left=%ss model=%s sdk=%s app=%s)",
            token_info.get("provider"),
            token_info.get("left"),
            info.get("model"),
            info.get("sdk_int"),
            info.get("app_version"),
        )
        return token_info["token"]
