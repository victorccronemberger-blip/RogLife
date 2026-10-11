"""integrations services: existing behavior with explicit dependencies."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('Path', '_PERSISTENCE_LOCK', '_apply_hostinger', '_hostinger_profiles', '_hostinger_routes', '_legacy_aws_credentials', '_migrate_legacy_integrations', '_storage_snapshot', 'config', 'configparser', 'ego4d', 'holoassist', 'load_secure_settings', 'os', 'readiness', 'save_secure_settings', 'uuid')


def _legacy_aws_credentials(_services) -> dict[str, str]:
    """Lê credenciais já configuradas sem expô-las para a resposta HTTP."""
    access = _services.os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
    secret = _services.os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
    if access and secret:
        return {
            "access_key_id": access,
            "secret_access_key": secret,
            "session_token": _services.os.environ.get("AWS_SESSION_TOKEN", "").strip(),
            "region": _services.config.EGO4D_AWS_REGION or "",
        }
    profile = _services.config.EGO4D_AWS_PROFILE or "default"
    configured = _services.os.environ.get("AWS_SHARED_CREDENTIALS_FILE", "").strip()
    candidates = [_services.Path(configured)] if configured else [
        _services.config.EGO4D_LOCAL_AWS_CREDENTIALS,
        _services.Path.home() / ".aws" / "credentials",
    ]
    parser = _services.configparser.RawConfigParser()
    for path in candidates:
        if not str(path) or not path.is_file():
            continue
        try:
            parser.read(path, encoding="utf-8")
        except (OSError, _services.configparser.Error):
            continue
        if not parser.has_section(profile):
            continue
        access = parser.get(profile, "aws_access_key_id", fallback="").strip()
        secret = parser.get(profile, "aws_secret_access_key", fallback="").strip()
        if access and secret:
            return {
                "access_key_id": access,
                "secret_access_key": secret,
                "session_token": parser.get(
                    profile, "aws_session_token", fallback="").strip(),
                "region": _services.config.EGO4D_AWS_REGION or "",
            }
    return {}

def _migrate_legacy_integrations(_services) -> dict[str, Any]:
    """Copia configurações existentes para o DPAPI, sem apagar os originais."""
    with _services._PERSISTENCE_LOCK:
        secure = _services.load_secure_settings(_services.config.INTEGRATIONS_PATH, strict=True)
        changed = False
        host = secure.get("hostinger")
        if not isinstance(host, dict) and _services.config.HOSTINGER_MAIL_TOKEN:
            host = {
                "token": _services.config.HOSTINGER_MAIL_TOKEN,
                "mailbox_id": _services.config.HOSTINGER_MAILBOX_ID,
            }
            secure["hostinger"] = host
            changed = True
        if isinstance(host, dict) and not isinstance(host.get("profiles"), list):
            token = str(host.get("token") or "").strip()
            secure["hostinger"] = {
                "profiles": ([{
                    "id": _services.uuid.uuid4().hex,
                    "name": "Caixa principal",
                    "token": token,
                    "mailbox_id": str(host.get("mailbox_id") or "").strip(),
                    "routes": [],
                }] if token else []),
            }
            host = secure["hostinger"]
            changed = True
        if isinstance(host, dict) and isinstance(host.get("profiles"), list):
            normalized_profiles = _services._hostinger_profiles(host)
            if normalized_profiles != host.get("profiles"):
                secure["hostinger"] = {"profiles": normalized_profiles}
                host = secure["hostinger"]
                changed = True
        if not isinstance(secure.get("ego4d"), dict):
            legacy = _services._legacy_aws_credentials()
            if legacy:
                secure["ego4d"] = legacy
                changed = True
        if changed:
            secure["schema"] = 2
            _services.save_secure_settings(_services.config.INTEGRATIONS_PATH, secure)
        if isinstance(secure.get("hostinger"), dict):
            _services._apply_hostinger(_services._hostinger_profiles(secure["hostinger"]))
        return secure

def _apply_ego4d(_services, values: dict[str, str]) -> None:
    access = values.get("access_key_id", "").strip()
    secret = values.get("secret_access_key", "").strip()
    session = values.get("session_token", "").strip()
    region = values.get("region", "").strip()
    _services.os.environ["AWS_ACCESS_KEY_ID"] = access
    _services.os.environ["AWS_SECRET_ACCESS_KEY"] = secret
    if session:
        _services.os.environ["AWS_SESSION_TOKEN"] = session
    else:
        _services.os.environ.pop("AWS_SESSION_TOKEN", None)
    _services.os.environ["EGO4D_AWS_PROFILE"] = ""
    if region:
        _services.os.environ["EGO4D_AWS_REGION"] = region
    else:
        _services.os.environ.pop("EGO4D_AWS_REGION", None)
    _services.config.EGO4D_AWS_PROFILE = ""
    _services.config.EGO4D_AWS_REGION = region

def _hostinger_routes(_services, value: Any) -> list[str]:
    if isinstance(value, str):
        values = value.replace(";", ",").split(",")
    elif isinstance(value, list):
        values = value
    else:
        values = []
    return sorted({
        str(item).strip().lower().lstrip("@")
        for item in values
        if str(item).strip()
    })

def _hostinger_profiles(_services, host: dict[str, Any]) -> list[dict[str, Any]]:
    raw = host.get("profiles")
    if not isinstance(raw, list):
        raw = [host] if host.get("token") else []
    profiles: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        token = str(item.get("token") or "").strip()
        if not token:
            continue
        profiles.append({
            "id": str(item.get("id") or _services.uuid.uuid4().hex).strip(),
            "name": str(item.get("name") or f"Caixa {index + 1}").strip(),
            "token": token,
            "mailbox_id": str(item.get("mailbox_id") or "").strip(),
            "routes": _services._hostinger_routes(item.get("routes")),
        })
    return profiles

def _apply_hostinger(_services, profiles: list[dict[str, Any]]) -> None:
    _services.config.HOSTINGER_MAIL_PROFILES = [dict(item) for item in profiles]
    primary = profiles[0] if profiles else {}
    token = str(primary.get("token") or "").strip()
    mailbox = str(primary.get("mailbox_id") or "").strip()
    _services.os.environ["HOSTINGER_MAIL_TOKEN"] = token
    _services.config.HOSTINGER_MAIL_TOKEN = token
    if mailbox:
        _services.os.environ["HOSTINGER_MAILBOX_ID"] = mailbox
    else:
        _services.os.environ.pop("HOSTINGER_MAILBOX_ID", None)
    _services.config.HOSTINGER_MAILBOX_ID = mailbox

def _integration_error(_services, exc: Exception, service: str) -> str:
    text = str(exc).lower()
    code = ""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str((response.get("Error") or {}).get("Code") or "").lower()
    if any(term in code or term in text for term in (
            "expiredtoken", "requestexpired", "expired")):
        return "A credencial expirou. Renove o acesso e salve as novas chaves."
    if any(term in code or term in text for term in (
            "invalidaccesskeyid", "signaturedoesnotmatch", "invalid token")):
        return "A credencial informada não foi reconhecida. Confira os campos."
    if "accessdenied" in code or "access denied" in text or "forbidden" in text:
        return "A credencial existe, mas não possui acesso ao conteúdo solicitado."
    if any(term in text for term in (
            "timeout", "connection", "network", "dns", "name resolution")):
        return f"Não foi possível conectar à {service}. Confira a internet e tente novamente."
    return f"A {service} não aceitou a configuração informada."

def _test_ego4d(_services, values: dict[str, str]) -> dict[str, Any]:
    import boto3
    client = boto3.client(
        "s3",
        region_name=values.get("region") or None,
        aws_access_key_id=values["access_key_id"],
        aws_secret_access_key=values["secret_access_key"],
        aws_session_token=values.get("session_token") or None,
    )
    response = client.get_object(
        Bucket=_services.ego4d.MANIFEST_BUCKET,
        Key=_services.ego4d.METADATA_KEY,
        Range="bytes=0-0",
    )
    body = response.get("Body")
    if body is not None:
        body.close()
    return {"ok": True, "message": "Acesso ao catálogo Ego4D confirmado."}

def _integration_snapshot(_services) -> dict[str, Any]:
    secure = _services._migrate_legacy_integrations()
    ego = secure.get("ego4d") if isinstance(secure.get("ego4d"), dict) else {}
    host = (secure.get("hostinger")
            if isinstance(secure.get("hostinger"), dict) else {})
    host_profiles = _services._hostinger_profiles(host)
    if not ego:
        ego = _services._legacy_aws_credentials()
    ego_dir = _services.config.MEDIA_DATA_DIR / "ego4d"
    holo_dir = _services.config.MEDIA_DATA_DIR / "holoassist"
    seed_names = (
        "video.index.json.gz", "video_compress.index.json.gz", "imu.index.json.gz",
    )
    holo_indexes = all((_services.holoassist._INDEX_SEED_DIR / name).is_file()
                       for name in seed_names)
    holo_catalog = (
        _services.holoassist.annotations_path().is_file()
        and (holo_dir / "data-splits-v1_2.zip").is_file()
    )
    storage = _services._storage_snapshot(include_path=True)
    access = str(ego.get("access_key_id") or "")
    host_token = str(host_profiles[0].get("token") if host_profiles else "")
    host_mailbox = str(host_profiles[0].get("mailbox_id") if host_profiles else "")
    return {
        "security": {
            "provider": "Windows DPAPI",
            "detail": "Segredos criptografados para este usuário do Windows.",
        },
        "ego4d": {
            "configured": bool(access and ego.get("secret_access_key")),
            "access_hint": f"••••{access[-4:]}" if len(access) >= 4 else "",
            "region": str(ego.get("region") or _services.config.EGO4D_AWS_REGION or "automática"),
            "catalog_ready": ((ego_dir / "ego4d.json").is_file()
                              and (ego_dir / "clips.csv").is_file()),
            "annotations_ready": _services.ego4d.has_timed_narrations(),
        },
        "hostinger": {
            "configured": bool(host_profiles),
            "connection_count": len(host_profiles),
            "token_hint": f"••••{host_token[-4:]}" if len(host_token) >= 4 else "",
            "mailbox_configured": bool(host_mailbox),
            "mailbox_hint": (
                f"••••{host_mailbox[-4:]}" if len(host_mailbox) >= 4 else ""
            ),
            "profiles": [{
                "id": item["id"],
                "name": item["name"],
                "token_hint": f"••••{item['token'][-4:]}",
                "mailbox_configured": bool(item["mailbox_id"]),
                "mailbox_hint": (
                    f"••••{item['mailbox_id'][-4:]}" if item["mailbox_id"] else ""
                ),
                "routes": item["routes"],
            } for item in host_profiles],
        },
        "holoassist": {
            "catalog_ready": holo_catalog,
            "indexes_ready": holo_indexes,
        },
        "runtime": {
            "ffmpeg_ready": _services.readiness._binary_works(_services.readiness.ffmpeg_bin()),
            "ffprobe_ready": _services.readiness._binary_works(_services.readiness.ffprobe_bin()),
            "browser_ready": _services.readiness._private_browser_present(),
        },
        "library": storage,
    }
