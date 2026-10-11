"""accelerator routes: existing behavior with explicit dependencies."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('HOLO_CACHE_RUNNER', 'MAX_DUR_S', 'RECOVERY', 'RUNNER', '_HEAVY_RUNNER_LOCK', '_accelerator_budget_gb', '_accelerator_durations', '_accelerator_module', '_accelerator_provider', '_accelerator_tasks', '_load_prefs', 'accelerator_catalog', 'campaign', 'config', 'ego_accelerator', 'holo_accelerator', 'holoassist', 'json', 'jsonify', 'load_json', 'math', 'nymeria_library_worker', 'request')


def _accelerator_provider(_services, value: Any) -> str | None:
    provider = str(value or "holoassist").strip().lower()
    if provider not in {"holoassist", "ego4d"}:
        return None
    return provider

def _accelerator_tasks(_services, provider: str) -> list[str]:
    if provider == "ego4d":
        return _services.ego_accelerator.task_names()
    return sorted(_services.holoassist.MINUTE_TASK_TYPES)

def _accelerator_module(_services, provider: str):
    return _services.ego_accelerator if provider == "ego4d" else _services.holo_accelerator

def _accelerator_budget_gb(_services, provider: str, raw: Any, *, default: int | None) -> int | None:
    if provider != "ego4d":
        return None
    if raw in (None, ""):
        raw = default
    if raw in (None, ""):
        return None
    try:
        _services.ego_accelerator.budget_bytes(raw)
        budget_gb = int(raw)
    except (TypeError, ValueError, OverflowError):
        raise ValueError
    if not 0 <= budget_gb <= _services.ego_accelerator.MAX_BUDGET_GB:
        raise ValueError
    return budget_gb

def _accelerator_durations(_services, values) -> tuple[float, float]:
    raw_minimum = values.get("min_dur_s", 60)
    raw_maximum = values.get("max_dur_s", 1800)
    if isinstance(raw_minimum, bool) or isinstance(raw_maximum, bool):
        raise ValueError
    minimum, maximum = float(raw_minimum), float(raw_maximum)
    if (not _services.math.isfinite(minimum) or not _services.math.isfinite(maximum)
            or not 60 <= minimum <= maximum <= _services.MAX_DUR_S):
        raise ValueError
    return minimum, maximum

def holo_cache_status(_services):
    provider = _services._accelerator_provider(_services.request.args.get("provider"))
    if provider is None:
        return _services.jsonify({"error": "provedor inválido (holoassist|ego4d)"}), 400
    module = _services._accelerator_module(provider)
    tasks = _services._accelerator_tasks(provider)
    task = str(_services.request.args.get("task") or module.DEFAULT_TASK)
    if task not in tasks:
        return _services.jsonify({"error": "tarefa inválida para o provedor"}), 400
    raw_limit = _services.request.args.get("limit")
    try:
        limit = None if raw_limit in (None, "", "0") else int(raw_limit)
        if limit is not None and not 1 <= limit <= 1000:
            raise ValueError
        budget_gb = _services._accelerator_budget_gb(
            provider, _services.request.args.get("budget_gb"),
            default=_services.ego_accelerator.configured_budget_gb(_services.ego_accelerator.DEFAULT_BUDGET_GB))
        min_free_gb = float(_services.request.args.get("min_free_gb", 50))
        if not _services.math.isfinite(min_free_gb) or not 5 <= min_free_gb <= 1000:
            raise ValueError
        min_dur_s, max_dur_s = _services._accelerator_durations(_services.request.args)
    except (TypeError, ValueError):
        return _services.jsonify({
            "error": "use cache em GB inteiros (0 para desativar), limite de 1 a 1000, reserva de 5 a 1000 GiB e duração entre 60 e 1800 s",
        }), 400
    if _services.request.args.get("live") == "1":
        # O plano completo percorre o catálogo e verifica cada arquivo.
        # Durante a execução basta consultar o runner e o estado salvo.
        return _services.jsonify({
            "provider": provider,
            "live": True,
            "runner": _services.HOLO_CACHE_RUNNER.snapshot(),
            "last_run": _services.load_json(module.state_path(), {}),
        })
    def read_cache(progress):
        progress("Conferindo conteúdo e arquivos preparados…")
        try:
            if provider == "ego4d":
                cache = module.cache_status(task, min_dur_s=min_dur_s, max_dur_s=max_dur_s,
                                            limit=limit, budget_gb=budget_gb,
                                            min_free_gb=min_free_gb)
            else:
                cache = module.cache_status(task, min_dur_s=min_dur_s, max_dur_s=max_dur_s,
                                            limit=limit)
        except (OSError, RuntimeError, ValueError, _services.json.JSONDecodeError) as exc:
            cache = {
                "provider": provider,
                "task": task,
                "total": 0,
                "ready": 0,
                "partial": 0,
                "pending": 0,
                "last_run": {},
                "catalog_error": str(exc),
            }
            if budget_gb is not None:
                try:
                    cache.update(_services.ego_accelerator.storage_limits(
                        budget_gb, min_free_gb=min_free_gb))
                except OSError:
                    cache.update({"budget_gb": 0, "max_budget_gb": 0})
        return {
            "provider": provider,
            "default_task": module.DEFAULT_TASK,
            "configured_budget_gb": _services.ego_accelerator.configured_budget_gb() if provider == "ego4d" else None,
            "cache": cache,
            "runner": _services.HOLO_CACHE_RUNNER.snapshot(),
            "tasks": tasks,
        }, 200
    runner = _services.HOLO_CACHE_RUNNER.snapshot()
    if _services.request.args.get("async") == "1":
        result, status = _services.accelerator_catalog.get(
            (provider, task, min_dur_s, max_dur_s, limit, budget_gb, min_free_gb,
             runner.get("run_id", 0), runner.get("state")), read_cache,
            scope="accelerator")
    else:
        result, status = read_cache(lambda message: None)
    # Catalog data may be cached; execution state must always be current.
    result = dict(result)
    result["runner"] = _services.HOLO_CACHE_RUNNER.snapshot()
    if isinstance(result.get("cache"), dict):
        result["cache"] = dict(result["cache"])
        result["cache"]["last_run"] = _services.load_json(module.state_path(), {})
    return _services.jsonify(result), status

def holo_cache_start(_services):
    body = _services.request.get_json(silent=True) or {}
    provider = _services._accelerator_provider(body.get("provider"))
    if provider is None:
        return _services.jsonify({"error": "provedor inválido (holoassist|ego4d)"}), 400
    if provider == "holoassist" and _services._load_prefs().get("holoassist_enabled") is False:
        return _services.jsonify({"error": "HoloAssist está desativado neste PC"}), 400
    module = _services._accelerator_module(provider)
    tasks = _services._accelerator_tasks(provider)
    task = str(body.get("task") or module.DEFAULT_TASK)
    if task not in tasks:
        return _services.jsonify({"error": "tarefa inválida para o provedor"}), 400
    try:
        raw_limit = body.get("limit")
        limit = None if raw_limit in (None, "", 0, "0") else int(raw_limit)
        if limit is not None and not 1 <= limit <= 1000:
            raise ValueError
        min_free_gb = float(body.get("min_free_gb", 50))
        if not _services.math.isfinite(min_free_gb) or not 5 <= min_free_gb <= 1000:
            raise ValueError
        budget_gb = _services._accelerator_budget_gb(
            provider, body.get("budget_gb"), default=_services.ego_accelerator.DEFAULT_BUDGET_GB)
        min_dur_s, max_dur_s = _services._accelerator_durations(body)
    except (TypeError, ValueError):
        return _services.jsonify({
            "error": "use limite entre 1 e 1000, reserva entre 5 e 1000 GiB, cache em GB inteiros (0 para desativar) e duração entre 60 e 1800 s",
        }), 400

    if provider == "ego4d" and budget_gb == 0:
        with _services._HEAVY_RUNNER_LOCK:
            if _services.HOLO_CACHE_RUNNER.running:
                return _services.jsonify({
                    "error": "pare o acelerador antes de desligar o cache",
                }), 409
            _services.ego_accelerator.remember_budget(0)
        return _services.jsonify({
            "ok": True,
            "cache_disabled": True,
            "runner": _services.HOLO_CACHE_RUNNER.snapshot(),
        })

    # Reserve the runner before expensive planning, so start and stop remain
    # responsive even with a cold catalog. warm_cache validates the plan.
    with _services._HEAVY_RUNNER_LOCK:
        if _services.RUNNER.running or _services.RECOVERY.running:
            return _services.jsonify({"error": "pare a campanha antes de iniciar o acelerador"}), 409
        if _services.nymeria_library_worker.running:
            return _services.jsonify({"error": "Aguarde a preparação da Biblioteca terminar."}), 409
        if _services.HOLO_CACHE_RUNNER.running:
            return _services.jsonify({"ok": True, "already_running": True,
                            "runner": _services.HOLO_CACHE_RUNNER.snapshot()})
        catalog = {}
        try:
            if provider == "ego4d":
                if not module.catalog_installed():
                    return _services.jsonify({"error": "catálogo Ego4D ainda não está instalado"}), 400
                catalog = module.storage_limits(budget_gb, min_free_gb=min_free_gb)
                budget_gb = catalog["budget_gb"]
                if budget_gb == 0:
                    return _services.jsonify({"error": "não há espaço disponível para cache acima da reserva de disco"}), 400
            _services.HOLO_CACHE_RUNNER.start(
                provider=provider, task=task, min_dur_s=min_dur_s, max_dur_s=max_dur_s, limit=limit,
                budget_gb=budget_gb, min_free_gb=min_free_gb)
        except (OSError, ValueError) as exc:
            return _services.jsonify({"error": str(exc)}), 400
        except RuntimeError as exc:
            return _services.jsonify({"error": str(exc)}), 409
    return _services.jsonify({
        "ok": True,
        "provider": provider,
        "cache": catalog,
        "runner": _services.HOLO_CACHE_RUNNER.snapshot(),
    })

def holo_cache_stop(_services):
    _services.HOLO_CACHE_RUNNER.stop()
    return _services.jsonify({"ok": True, "runner": _services.HOLO_CACHE_RUNNER.snapshot()})

def storage_cleanup(_services):
    """Limpeza manual confinada aos caches de mídia conhecidos."""
    body = _services.request.get_json(silent=True) or {}
    provider = str(body.get("provider") or "all").strip().lower()
    if provider not in {"ego4d", "holoassist", "all"}:
        return _services.jsonify({"error": "provedor inválido (ego4d|holoassist|all)"}), 400
    with _services._HEAVY_RUNNER_LOCK:
        if _services.RUNNER.running or _services.RECOVERY.running or _services.HOLO_CACHE_RUNNER.running or _services.nymeria_library_worker.running:
            return _services.jsonify({
                "error": "pare a campanha e o acelerador antes de limpar a mídia",
            }), 409
        try:
            result = _services.campaign.cleanup_media_cache(_services.config.MEDIA_DATA_DIR / "ego4d", provider=provider)
        except (ValueError,OSError):
            return _services.jsonify({"error_code":"cleanup_state_unreadable",
                            "error":"Os registros de envio precisam ser revisados antes de limpar a mídia."}),409
    return _services.jsonify({"ok": not result["errors"], "provider": provider, **result})
