"""library nymeria routes: existing behavior with explicit dependencies."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('HOLO_CACHE_RUNNER', 'Path', 'RECOVERY', 'RUNNER', '_HEAVY_RUNNER_LOCK', 'campaign_closing_response', 'campaign_drain', 'campaign_verifications', 'hashlib', 'json', 'jsonify', 'library_task_catalog', 'math', 'nymeria_catalog', 'nymeria_library', 'nymeria_library_worker', 'portable_setup', 'request', 'task_catalog', 'start_nymeria_library_work')


def get_nymeria_library(_services):
    try:
        result = _services.nymeria_library.inventory_with_summary(limit=1)
        result["task_names"] = _services.library_task_catalog.library_task_names()
        result["task_catalog_source"] = "local_snapshot_requires_campaign_preflight"
    except (OSError, ValueError, RuntimeError):
        return _services.jsonify({"error": "Não foi possível ler o catálogo Nymeria."}), 503
    return _services.jsonify({**result, "worker": _services.nymeria_library_worker.snapshot(), "setup": _services.portable_setup})

def browse_nymeria_library(_services):
    try:
        query = _services.request.args.get("q", "")
        state = _services.request.args.get("state", "all")
        limit = int(_services.request.args.get("limit", 50))
        offset = int(_services.request.args.get("offset", 0))
        if len(query) > 200 or not 1 <= limit <= 100 or not 0 <= offset <= 100000:
            raise ValueError
        result = _services.nymeria_library.inventory_with_summary(query=query, state=state,
                                                         limit=limit, offset=offset)
        result["task_names"] = _services.library_task_catalog.library_task_names()
        result["task_catalog_source"] = "local_snapshot_requires_campaign_preflight"
    except (OSError, ValueError, RuntimeError):
        return _services.jsonify({"error": "Filtros inválidos ou catálogo Nymeria indisponível."}), 400
    return _services.jsonify({**result, "worker": _services.nymeria_library_worker.snapshot(), "setup": _services.portable_setup})

def nymeria_library_operation(_services):
    return _services.jsonify(_services.nymeria_library_worker.snapshot())

def stop_nymeria_library_operation(_services):
    _services.nymeria_library_worker.stop()
    return _services.jsonify({"ok": True, "worker": _services.nymeria_library_worker.snapshot()})

def start_nymeria_library_work(_services, operation, work):
    with _services._HEAVY_RUNNER_LOCK:
        if _services.campaign_drain["requested"]:
            return _services.campaign_closing_response()
        if (_services.RUNNER.running or _services.RECOVERY.running or _services.HOLO_CACHE_RUNNER.running or _services.nymeria_library_worker.running
                or _services.task_catalog.busy_for_scope("provider-index")):
            return _services.jsonify({"error": "Aguarde a campanha, recuperação ou preparação atual terminar."}), 409
        if _services.campaign_drain["requests"] or _services.campaign_verifications.busy or _services.nymeria_catalog.busy:
            return _services.jsonify({"error": "Aguarde a verificação ou o planejamento terminar antes de alterar a Biblioteca."}), 409
        worker = _services.nymeria_library_worker.start(operation, work, root=str(_services.nymeria_library.data_root()))
    return _services.jsonify({"ok": True, "worker": worker}), 202

def import_nymeria_library_manifest(_services):
    body = _services.request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("path"), str) or not body["path"].strip():
        return _services.jsonify({"error": "Escolha o JSON de URLs do Nymeria."}), 400
    path = _services.Path(body["path"])
    try:
        valid_path = path.is_file() and path.stat().st_size <= 32 * 1024 * 1024
    except OSError:
        valid_path = False
    if not valid_path:
        return _services.jsonify({"error": "Manifesto Nymeria ausente ou acima de 32 MiB."}), 400
    def import_and_sync(progress, stopped):
        _services.nymeria_library.import_manifest(path)
        _services.portable_setup.clear()
        _services.portable_setup.update(state="configured", message="Manifesto NymeriaPlus importado.")
        _services.nymeria_library._check_stop(stopped)
        progress("Manifesto importado. Sincronizando metadados e anotações; vídeos serão adquiridos sob demanda.")
        return _services.nymeria_library.sync_catalog(progress=progress, should_stop=stopped)
    return _services.start_nymeria_library_work("import", import_and_sync)

def sync_nymeria_library_catalog(_services):
    body = _services.request.get_json(silent=True)
    if not isinstance(body, dict) or body:
        return _services.jsonify({"error": "A atualização do catálogo não aceita filtros."}), 400
    return _services.start_nymeria_library_work("sync", lambda progress, stopped:
        _services.nymeria_library.sync_catalog(progress=progress, should_stop=stopped))

def plan_nymeria_library_expansion(_services):
    with _services._HEAVY_RUNNER_LOCK:
        if (_services.nymeria_library_worker.running
                or _services.task_catalog.busy_for_scope("provider-index")):
            return _services.jsonify({"error": "Aguarde a preparação terminar antes de planejar outras fontes."}), 409
    body = _services.request.get_json(silent=True)
    try:
        if not isinstance(body, dict):
            raise ValueError
        tasks = body.get("task_names")
        if (not isinstance(tasks, list) or not tasks or len(tasks) > 100
                or any(not isinstance(name, str) or not name.strip() for name in tasks)):
            raise ValueError
        if any(isinstance(body.get(key), bool) for key in
               ("min_dur_s", "max_dur_s", "target_seconds", "min_free_gb")):
            raise ValueError
        minimum = float(body.get("min_dur_s", 300))
        maximum = float(body.get("max_dur_s", 1800))
        target = float(body.get("target_seconds", 28800))
        reserve_gb = float(body.get("min_free_gb", 50))
        if (any(not _services.math.isfinite(v) for v in (minimum, maximum, target, reserve_gb))
                or not 60 <= minimum <= maximum <= 1800 or not 0 < target <= 43200
                or not 5 <= reserve_gb <= 1000):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        return _services.jsonify({"error": "Selecione tarefas, duração entre 60 e 1800 s e meta de até 12 h."}), 400
    def plan(progress):
        progress("Procurando conteúdo Nymeria nas narrações do catálogo completo…")
        try:
            return _services.nymeria_library.plan_expansion(tasks, min_dur_s=minimum,
                max_dur_s=maximum, target_seconds=target, progress=progress,
                min_free_bytes=int(reserve_gb * 1024 ** 3)), 200
        except (OSError, ValueError, RuntimeError):
            return {"error": "Não foi possível planejar o conteúdo. Atualize o catálogo Nymeria."}, 503
    digest = _services.hashlib.sha256(_services.json.dumps(body, sort_keys=True).encode()).hexdigest()
    with _services._HEAVY_RUNNER_LOCK:
        if (_services.nymeria_library_worker.running
                or _services.task_catalog.busy_for_scope("provider-index")):
            return _services.jsonify({"error": "Aguarde a preparação terminar antes de planejar outras fontes."}), 409
        generation = _services.nymeria_library_worker.snapshot().get("run_id")
        result, status = _services.nymeria_catalog.get((str(_services.nymeria_library.data_root()), generation, digest), plan,
                                            scope="nymeria-expansion")
    return _services.jsonify(result), status

def acquire_nymeria_library_sequences(_services):
    body = _services.request.get_json(silent=True)
    if not isinstance(body, dict):
        return _services.jsonify({"error": "Selecione as sequências do plano Nymeria."}), 400
    seq_ids = body.get("seq_ids")
    if (not isinstance(seq_ids, list) or not seq_ids or len(seq_ids) > 1100
            or any(not isinstance(value, str) or not value for value in seq_ids)):
        return _services.jsonify({"error": "Selecione as sequências do plano Nymeria."}), 400
    try:
        reserve_gb = body.get("min_free_gb", 50)
        if isinstance(reserve_gb, bool):
            raise ValueError
        reserve_gb = float(reserve_gb)
        if not _services.math.isfinite(reserve_gb) or not 5 <= reserve_gb <= 1000:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        return _services.jsonify({"error": "A reserva de espaço deve estar entre 5 e 1000 GiB."}), 400
    # IDs are checked against the imported catalog inside the worker. A
    # receipt supplied by the browser never becomes an arbitrary URL/path.
    return _services.start_nymeria_library_work("download", lambda progress, stopped:
        _services.nymeria_library.acquire_sequences(seq_ids, progress=progress, should_stop=stopped,
                                          min_free_bytes=int(reserve_gb * 1024 ** 3)))
