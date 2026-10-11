"""storage routes: existing behavior with explicit dependencies."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('Path', '_local_dataset_provider', '_storage_snapshot', 'config', 'jsonify', 'local_media_inventory', 'prepared_library', 'readiness', 'request', 'shutil')


def get_readiness(_services):
    try:
        provider = _services._local_dataset_provider(
            _services.request.args.get("dataset")
        )
    except ValueError:
        return _services.jsonify({"error": "Dataset inválido.", "code": "invalid_dataset"}), 400
    try:
        result = _services.readiness.campaign_readiness(provider)
    except (ValueError, OSError, RuntimeError):
        return _services.jsonify({"error": "Não foi possível verificar a prontidão. Confira a biblioteca local e as ferramentas configuradas.",
                        "code": "readiness_unavailable"}), 400
    result["storage"] = _services._storage_snapshot(include_path=False)
    return _services.jsonify(result)

def get_storage_library(_services):
    return _services.jsonify(_services._storage_snapshot(include_path=True))

def browse_local_media_library(_services):
    root = _services.config.MEDIA_DATA_DIR
    try:
        query = _services.request.args.get("q", "")
        provider = _services.request.args.get("provider", "all")
        kind = _services.request.args.get("kind", "all")
        limit = int(_services.request.args.get("limit", 50))
        offset = int(_services.request.args.get("offset", 0))
        refresh = _services.request.args.get("refresh", "")
        if (len(query) > 200 or provider not in {"all", "ego4d", "holoassist", "nymeria", "local"}
                or kind not in {"all", "video", "sensor", "sidecar", "catalog", "derivative"}
                or not 1 <= limit <= 100 or not 0 <= offset <= 100000 or len(refresh) > 80):
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        return _services.jsonify({"error": "Filtros inválidos para os arquivos da Biblioteca."}), 400

    def read_local_files(progress):
        progress("Lendo os arquivos da Biblioteca local…")
        try:
            result = _services.prepared_library.list_local_media(
                root, query, provider=provider, kind=kind, limit=limit, offset=offset)
            disk_path = _services.Path(root)
            while not disk_path.exists() and disk_path != disk_path.parent:
                disk_path = disk_path.parent
            result["disk_free_bytes"] = _services.shutil.disk_usage(disk_path).free
            return result, 200
        except (OSError, ValueError, RuntimeError):
            return {"error": "Não foi possível ler os arquivos da Biblioteca. Tente atualizar a lista."}, 503

    if _services.request.args.get("async") != "1":
        result, status = read_local_files(lambda message: None)
    else:
        # File enumeration belongs to the worker, including with a cold
        # library. A nonce is idempotent while the request is being polled.
        result, status = _services.local_media_inventory.get(
            (str(root), query, provider, kind, limit, offset, refresh),
            read_local_files, scope="local-media-library")
    return _services.jsonify(result), status
