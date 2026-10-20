"""Entrada mínima do serviço local para o bundle PyInstaller."""
import os
from pathlib import Path
import sys

from moneymin.web import main


def configure_packaged_mobile_tools(*, runtime_root=None, environ=None):
    """Expose the private adb bundled beside the service without running it."""
    env = os.environ if environ is None else environ
    root = Path(runtime_root) if runtime_root is not None else Path(sys.executable).resolve().parent
    tools = root / "tools" / "platform-tools"
    adb = tools / "adb.exe"
    if not adb.is_file():
        return None

    configured_adb = str(env.get("QMONEY_ADB_PATH", "")).strip()
    preferred_tools = []
    if configured_adb and Path(configured_adb).is_file():
        preferred_tools.append(Path(configured_adb).resolve().parent)
    preferred_tools.append(tools)
    preferred_tools = list(dict.fromkeys(preferred_tools))
    path_entries = [entry for entry in env.get("PATH", "").split(os.pathsep) if entry]
    preferred_keys = {
        os.path.normcase(os.path.abspath(str(path))) for path in preferred_tools
    }
    remaining = [
        entry for entry in path_entries
        if os.path.normcase(os.path.abspath(entry)) not in preferred_keys
    ]
    env["PATH"] = os.pathsep.join([*(str(path) for path in preferred_tools), *remaining])
    if not configured_adb:
        env["QMONEY_ADB_PATH"] = str(adb)
    return adb


if __name__ == "__main__":
    configure_packaged_mobile_tools()
    main()
