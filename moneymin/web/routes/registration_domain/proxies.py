"""Route handlers grouped by account workflow."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('BALANCES_RUNNER', 'RECOVERY', 'RUNNER', 'jsonify', 'request')


def list_registration_proxies(_services):
    from moneymin import registration_proxy
    return _services.jsonify({"proxies": registration_proxy.public_rows()})

def import_registration_proxies(_services):
    from moneymin import registration_proxy
    if _services.RUNNER.running or _services.RECOVERY.running or _services.BALANCES_RUNNER.running:
        return _services.jsonify({"error": "Pare a operação antes de importar proxies."}), 409
    try:
        result = registration_proxy.import_text((_services.request.get_json(silent=True) or {}).get("text"))
    except (OSError, ValueError) as exc:
        return _services.jsonify({"error": str(exc)}), 400
    return _services.jsonify({"ok": True, **result})
