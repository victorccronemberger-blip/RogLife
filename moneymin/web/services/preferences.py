"""preferences services: existing behavior with explicit dependencies."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('JsonStateError', 'PREFS_PATH', '_PERSISTENCE_LOCK', '_invalid_local_state', '_load_prefs', '_removed_accounts', '_removed_accounts_path', '_save_prefs', '_tree_size', '_validate_local_document', '_validate_local_prefs', 'account_bans', 'campaign', 'config', 'json', 'load_json_state', 'math', 'org_policy', 'os', 'save_json', 'shutil')


def _tree_size(_services, path: Path) -> tuple[int, int]:
    """Tamanho/arquivos sem seguir links; falhas pontuais não quebram o painel."""
    total = files = 0
    if not path.exists():
        return total, files
    pending = [path]
    while pending:
        try:
            with _services.os.scandir(pending.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            # DirEntry reuses Windows enumeration metadata;
                            # Path.stat issues another filesystem query per file.
                            total += entry.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, files

def _storage_snapshot(_services, *, include_path: bool = True) -> dict[str, Any]:
    data_bytes, data_files = _services._tree_size(_services.config.MEDIA_DATA_DIR)
    ego_bytes, ego_files = _services._tree_size(_services.config.MEDIA_DATA_DIR / "ego4d")
    holo_bytes, holo_files = _services._tree_size(_services.config.MEDIA_DATA_DIR / "holoassist")
    try:
        usage = _services.shutil.disk_usage(_services.config.LIBRARY_ROOT)
        free_bytes, total_bytes = usage.free, usage.total
    except OSError:
        free_bytes = total_bytes = 0
    result: dict[str, Any] = {
        "ready": (
            (_services.config.MEDIA_DATA_DIR / "ego4d" / "timed_narrations.jsonl").exists()
            or (_services.config.MEDIA_DATA_DIR / "ego4d" / "clip_narrations.json").exists()
            or (_services.config.MEDIA_DATA_DIR / "holoassist").exists()
        ),
        "data_bytes": data_bytes,
        "data_files": data_files,
        "ego4d_bytes": ego_bytes,
        "ego4d_files": ego_files,
        "holoassist_bytes": holo_bytes,
        "holoassist_files": holo_files,
        "free_bytes": free_bytes,
        "disk_bytes": total_bytes,
    }
    if include_path:
        result.update({
            "root": str(_services.config.LIBRARY_ROOT),
            "data_dir": str(_services.config.MEDIA_DATA_DIR),
        })
    return result

def _finite_state_number(_services, value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return _services.math.isfinite(value)
    except (ValueError, OverflowError):
        return False

def _invalid_local_state(_services) -> None:
    raise _services.JsonStateError("Estado local inválido ou ilegível. Preserve o arquivo e restaure um backup válido antes de continuar.") from None

def _validate_local_document(_services, value: Any) -> None:
    try:
        _services.json.dumps(value, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError):
        _services._invalid_local_state()

def _validate_local_prefs(_services, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        _services._invalid_local_state()
    if (("org_keys" in value and (not isinstance(value["org_keys"], dict)
            or any(not isinstance(email, str) or not isinstance(org, str)
                   for email, org in value["org_keys"].items())))
            or ("selected_accounts" in value and (not isinstance(value["selected_accounts"], list)
                or any(not isinstance(email, str) for email in value["selected_accounts"])))
            or ("holoassist_enabled" in value and type(value["holoassist_enabled"]) is not bool)):
        _services._invalid_local_state()
    return value

def _load_prefs(_services) -> dict[str, Any]:
    return _services._validate_local_prefs(_services.load_json_state(_services.PREFS_PATH, {}))

def _save_prefs(_services, prefs: dict[str, Any]) -> None:
    with _services._PERSISTENCE_LOCK:
        _services._validate_local_document(prefs)
        _services._validate_local_prefs(prefs)
        _services._load_prefs()
        _services.save_json(_services.PREFS_PATH, prefs)

def _local_dataset_provider(_services, value: Any) -> str:
    provider = _services.campaign.normalize_dataset_provider(value)
    if _services._load_prefs().get("holoassist_enabled") is False:
        if provider == "holoassist":
            raise ValueError("HoloAssist está desativado neste PC")
        if provider == "all":
            return "ego4d"
    return provider

def _cache_org_key(_services, email: str, org_key: str) -> None:
    """Persiste somente uma organização já confirmada pela política da conta."""
    if org_key != _services.org_policy.target_org_key(email):
        raise ValueError("Organização recusada pela política da conta.")
    with _services._PERSISTENCE_LOCK:
        prefs = _services._load_prefs()
        prefs.setdefault("org_keys", {})[email] = org_key
        _services._save_prefs(prefs)

def _removed_accounts_path(_services) -> Path:
    """Registro local que impede a migração de ressuscitar contas removidas."""
    return _services.config.DATA_DIR / "removed_accounts.json"

def _removed_accounts(_services) -> set[str]:
    value = _services.load_json_state(_services._removed_accounts_path(), {})
    emails = value.get("emails", [])
    if not isinstance(emails, list) or any(not isinstance(email, str) or not email.strip() for email in emails):
        _services._invalid_local_state()
    return {
        str(email).strip().casefold()
        for email in emails
        if str(email).strip()
    } | _services.account_bans.banned_emails()

def _set_account_removed(_services, email: str, removed: bool) -> None:
    normalized = email.strip().casefold()
    if not normalized:
        return
    with _services._PERSISTENCE_LOCK:
        emails = _services._removed_accounts()
        if removed:
            emails.add(normalized)
        else:
            emails.discard(normalized)
        _services.save_json(_services._removed_accounts_path(), {
            "schema": 1,
            "emails": sorted(emails),
        })
