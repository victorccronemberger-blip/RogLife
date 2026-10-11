"""library ego4d routes: existing behavior with explicit dependencies."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('ego4d_library', 'jsonify', 'math', 'original_library_index', 'original_library_paths', 'prepared_library', 'prepared_library_inventory', 'request', 'sqlite3')


def browse_prepared_library(_services):
    root, _ = _services.original_library_paths()
    try:
        query = _services.request.args.get("q", "")
        state = _services.request.args.get("state", "all")
        minimum = float(_services.request.args.get("min_s", 0))
        raw_maximum = _services.request.args.get("max_s")
        maximum = float(raw_maximum) if raw_maximum else None
        limit = int(_services.request.args.get("limit", 50))
        offset = int(_services.request.args.get("offset", 0))
        refresh = _services.request.args.get("refresh", "")
        if (len(query) > 200 or state not in {"all", "ready", "partial", "missing", "stale"}
                or not _services.math.isfinite(minimum) or minimum < 0
                or maximum is not None and (not _services.math.isfinite(maximum) or maximum < minimum)
                or not 1 <= limit <= 100 or not 0 <= offset <= 100000 or len(refresh) > 80):
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        return _services.jsonify({"error": "Filtros inválidos para os recortes locais."}), 400

    def read_inventory(progress):
        progress("Conferindo os recortes e arquivos locais…")
        try:
            result = _services.prepared_library.list_prepared_clips(
                root, query, state=state, minimum_s=minimum, maximum_s=maximum,
                limit=limit, offset=offset, force_refresh=bool(refresh))
            return result, 200
        except (OSError, ValueError, RuntimeError):
            return {"error": "Não foi possível conferir os recortes locais. Atualize a Biblioteca."}, 503

    if _services.request.args.get("async") != "1":
        result, status = read_inventory(lambda message: None)
    else:
        try:
            signature = _services.prepared_library.inventory_signature(root)
        except (OSError, ValueError, RuntimeError):
            return _services.jsonify({"error": "Não foi possível ler a Biblioteca local."}), 503
        result, status = _services.prepared_library_inventory.get(
            (str(root), signature, query, state, minimum, maximum, limit, offset, refresh),
            read_inventory, scope="prepared-library")
    return _services.jsonify(result), status

def get_original_library_summary(_services):
    root, index = _services.original_library_paths()
    try:
        return _services.jsonify(_services.ego4d_library.library_summary(index, root))
    except (OSError, ValueError, _services.sqlite3.Error):
        return _services.jsonify({"state": "corrupt", "needs_index": True})

def index_original_library(_services):
    root, index = _services.original_library_paths()
    if not all((root / name).is_file() for name in ("ego4d.json", "clips.csv")):
        return _services.jsonify({"error": "Prepare o catálogo Ego4D em Integrações primeiro."}), 400
    try:
        current = _services.ego4d_library.library_summary(index, root)
        if not current.get("needs_index"):
            return _services.jsonify(current)
    except (OSError, ValueError, _services.sqlite3.Error):
        pass
    try:
        sources = tuple((name, (root / name).stat().st_size, (root / name).stat().st_mtime_ns)
                        for name in ("ego4d.json", "clips.csv", "timed_narrations.jsonl")
                        if (root / name).is_file())
    except OSError:
        return _services.jsonify({"error": "Não foi possível ler o catálogo local."}), 503
    def build(progress):
        try:
            _services.ego4d_library.index_library(root, index, progress=progress)
            return _services.ego4d_library.library_summary(index, root), 200
        except (OSError, ValueError, _services.sqlite3.Error):
            return {"error": "Falha ao indexar os metadados; fontes e índice anterior preservados."}, 500
    result, status = _services.original_library_index.get((str(root), sources), build, scope="original-library")
    return _services.jsonify(result), status

def browse_original_library(_services):
    root, index = _services.original_library_paths()
    try:
        state = _services.ego4d_library.library_summary(index, root)
        if state.get("needs_index"):
            return _services.jsonify({"error": "Atualize o índice da biblioteca primeiro.", **state}), 409
        minimum = float(_services.request.args.get("min_s", 0))
        maximum = _services.request.args.get("max_s")
        imu_filter = _services.request.args.get("imu", "all")
        if imu_filter not in {"all", "declared"}:
            raise ValueError("Filtro de sensores inválido.")
        result = _services.ego4d_library.browse_library(index, _services.request.args.get("q", ""), minimum_s=minimum,
            maximum_s=float(maximum) if maximum else None, imu_only=imu_filter == "declared",
            limit=int(_services.request.args.get("limit", 50)), offset=int(_services.request.args.get("offset", 0)))
        return _services.jsonify(result)
    except (ValueError, _services.sqlite3.OperationalError):
        return _services.jsonify({"error": "Filtros inválidos ou índice indisponível; atualize a biblioteca."}), 400
    except (OSError, _services.sqlite3.Error):
        return _services.jsonify({"error": "Não foi possível consultar a biblioteca local."}), 503
