"""campaign execution routes: existing behavior with explicit dependencies."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('CampaignStateLeaseError', 'HOLO_CACHE_RUNNER', 'RECOVERY', 'RUNNER', '_HEAVY_RUNNER_LOCK', 'accelerator_catalog', 'campaign', 'campaign_drain', 'campaign_reset', 'campaign_start_store', 'campaign_state_lease', 'campaign_verifications', 'jsonify', 'local_media_inventory', 'nymeria_catalog', 'nymeria_library_worker', 'original_library_index', 'original_preflights', 'original_starts', 'preflight_lock', 'preflights', 'prepared_library_inventory', 'recovery_catalog', 'request', 'task_catalog')


def campaign_start_status(_services, start_id):
    try:
        data=_services.campaign_start_store.public_status(start_id)
        row=_services.campaign_start_store.lookup(start_id)
        if row is not None:
            from ...start_execution import execution_evidence
            snap=_services.RUNNER.snapshot()
            running_id=snap.get('start_request_id') if isinstance(snap,dict) else None
            data.update(execution_evidence(row,running_id=running_id,thread_running=_services.RUNNER.running is True))
        else:
            data.update(execution_state='review',terminal=False,log_name=None,delivery_confirmed=False)
        return _services.jsonify(data)
    except (ValueError, OSError):
        return _services.jsonify({"ok":False,"error_code":"start_state_unreadable",
                        "error":"Preserve o estado local e revise o registro de início."}), 409

def campaign_current(_services):
    try:
        since = int(_services.request.args.get("since", 0))
    except ValueError:
        since = 0
    return _services.jsonify(_services.RUNNER.snapshot(since=since))

def pause_campaign(_services):
    try:
        _services.RUNNER.pause()
    except RuntimeError as exc:
        return _services.jsonify({"error": str(exc)}), 409
    return _services.jsonify({"ok": True, "pause_requested": True})

def resume_campaign(_services):
    try:
        _services.RUNNER.resume()
    except RuntimeError as exc:
        return _services.jsonify({"error": str(exc)}), 409
    return _services.jsonify({"ok": True, "pause_requested": False})

def campaign_stop(_services):
    _services.RUNNER.stop()
    return _services.jsonify({"ok": True, "state": _services.RUNNER.state})

def campaign_close_drain(_services):
    # The HTTP acknowledgement is not a worker-completion receipt. Keep
    # the owned service alive until engine cleanup and local commits return.
    with _services._HEAVY_RUNNER_LOCK:
        _services.campaign_drain["requested"] = True
        _services.RUNNER.stop()
        _services.nymeria_library_worker.stop()
        recovery_thread = getattr(_services.RECOVERY, "_thread", None)
        recovery_active = (_services.RECOVERY.running
                           or (recovery_thread is not None and recovery_thread.is_alive()))
        ready = (not _services.RUNNER.running and not recovery_active and not _services.nymeria_library_worker.running
                 and _services.campaign_drain["requests"] == 0)
        return _services.jsonify({"ok": True, "draining": True, "ready": bool(ready)})

def reset_campaign_state(_services):
    body = _services.request.get_json(silent=True) or {}
    if body:
        return _services.jsonify({"error": "Reset completo não aceita filtros ou caminhos."}), 400
    try:
        with _services.campaign_state_lease(exclusive=True), _services._HEAVY_RUNNER_LOCK:
            workers = (_services.RUNNER, _services.RECOVERY, _services.HOLO_CACHE_RUNNER, _services.nymeria_library_worker)
            catalogs = (_services.task_catalog, _services.campaign_verifications, _services.accelerator_catalog,
                        _services.recovery_catalog, _services.original_library_index, _services.prepared_library_inventory,
                        _services.local_media_inventory, _services.nymeria_catalog)
            if (_services.campaign_drain["requested"] or _services.campaign_drain["requests"]
                    or any(worker.running or (getattr(worker, "_thread", None) is not None
                               and worker._thread.is_alive()) for worker in workers)
                    or any(catalog.busy for catalog in catalogs)):
                return _services.jsonify({"error_code": "campaign_reset_busy",
                                "error": "Pare a campanha ou recuperação e aguarde as operações e consultas terminarem antes do Reset completo."}), 409
            result = _services.campaign_reset.erase_local_campaigns()
            _services.RUNNER.reset_idle()
            _services.RECOVERY.reset_idle()
            with _services.preflight_lock:
                _services.preflights.clear()
                _services.original_preflights.clear()
                _services.original_starts.clear()
            for catalog in catalogs:
                catalog.clear_idle()
            # Invalidate derived selection results; raw media and evidence
            # in the Library stay intact and are re-read on demand.
            _services.campaign._ranked_pools_cached.cache_clear()
            _services.campaign._duration_ranked_pools.cache_clear()
            _services.campaign._nymeria_windows.cache_clear()
    except _services.CampaignStateLeaseError:
        return _services.jsonify({"error_code": "campaign_reset_busy",
                        "error": "Aguarde as operações locais terminarem antes do Reset completo."}), 409
    except _services.campaign_reset.CampaignResetError as exc:
        return _services.jsonify({"ok": False, "error_code": exc.code,
                        "error": str(exc), "partial": exc.partial}), 409
    return _services.jsonify({"ok": True, "state": "idle", **result})
