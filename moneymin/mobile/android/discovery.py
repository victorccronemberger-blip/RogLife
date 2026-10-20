"""Resolve the configured Android Debug Bridge executable."""
from __future__ import annotations


def find_adb(*, windows, os_module, path_type, shutil_module) -> str | None:
    """Check explicit tool locations without scanning private settings."""
    name = "adb.exe" if windows else "adb"
    configured = os_module.environ.get("QMONEY_ADB_PATH", "").strip()
    if configured:
        candidate = path_type(configured).expanduser()
        if candidate.is_dir():
            candidate /= name
        if candidate.is_file():
            return str(candidate)

    located = shutil_module.which("adb")
    if located:
        return located

    roots = []
    for variable in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        value = os_module.environ.get(variable, "").strip()
        if value:
            roots.append(path_type(value).expanduser())
    local_app_data = os_module.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        roots.append(path_type(local_app_data) / "Android" / "Sdk")
    home = path_type.home()
    roots.extend((home / "AppData" / "Local" / "Android" / "Sdk",
                  home / "Android" / "Sdk", home / "Library" / "Android" / "sdk"))
    for root in roots:
        candidate = root / "platform-tools" / name
        if candidate.is_file():
            return str(candidate)
    return None
