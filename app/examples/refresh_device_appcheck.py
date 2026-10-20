"""Captura App Check Play Integrity (RogLife) para o Finally.

Uso:
  $env:QMONEY_APPCHECK_SOURCE='device'
  python app/examples/refresh_device_appcheck.py
  python app/examples/refresh_device_appcheck.py --force
  python app/examples/refresh_device_appcheck.py --serial RQCT804KN2N
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QMONEY_APPCHECK_SOURCE", "device")


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh device App Check for Finally")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--serial", default=None)
    args = parser.parse_args()
    if args.serial:
        os.environ["QMONEY_APPCHECK_ADB_SERIAL"] = args.serial

    from moneymin import appcheck
    from moneymin.roglife_appcheck.config import config_path
    from moneymin.roglife_appcheck.provider import cache_path, obtain_token

    print("=== refresh device App Check ===")
    print(f"roglife config: {config_path()}")
    print(f"cache: {cache_path()}")
    token = obtain_token(force=args.force, serial=args.serial)
    if not token:
        print("FALHA: nenhum token obtido")
        return 2
    status = appcheck.app_check_status()
    print(json.dumps({
        "ok": True,
        "token_len": len(token),
        "cache": str(cache_path()),
        "status": {
            "configured": status.get("configured"),
            "token_available": status.get("token_available"),
            "source": status.get("source"),
            "device": status.get("device"),
        },
    }, indent=2, ensure_ascii=False))
    header = appcheck.get_app_check_header()
    print(f"header_keys: {list(header.keys())}")
    return 0 if header.get("X-Firebase-AppCheck") else 3


if __name__ == "__main__":
    raise SystemExit(main())
