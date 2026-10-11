"""preferences routes: existing behavior with explicit dependencies."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('_PERSISTENCE_LOCK', '_load_prefs', '_save_prefs', 'jsonify', 'request')


def get_prefs(_services):
    return _services.jsonify(_services._load_prefs())

def put_prefs(_services):
    body = _services.request.get_json(silent=True)
    if not isinstance(body, dict):
        return _services.jsonify({"error": "body JSON inválido"}), 400
    with _services._PERSISTENCE_LOCK:
        prefs = _services._load_prefs()
        prefs.update(body)
        _services._save_prefs(prefs)
    return _services.jsonify(prefs)
