"""Explicit provider indexing outside campaign/account admission."""
import hashlib
import re

from ...background_work import report_progress
from ...catalog_policy import persist_catalog_indexes

REQUIRES = ("campaign", "task_catalog", "request", "jsonify", "config", "_parse_duration_range",
            "nymeria_library", "_HEAVY_RUNNER_LOCK", "RUNNER", "RECOVERY", "HOLO_CACHE_RUNNER",
            "nymeria_library_worker", "campaign_drain", "campaign_verifications", "nymeria_catalog",
            "campaign_closing_response")


def _selection(services):
    provider = services.campaign.normalize_dataset_provider(services.request.args.get("dataset", "ambos"))
    if provider not in {"ambos", "ego4d", "nymeria"}:
        raise ValueError("Escolha Ego4D, NymeriaPlus ou os dois provedores.")
    minimum, maximum = services._parse_duration_range(services.request.args)
    key = ("provider-index", str(services.config.MEDIA_DATA_DIR), provider, minimum, maximum,
           services.campaign.nymeria._rules_digest())
    return provider, minimum, maximum, key


def get_provider_catalog(_services):
    try:
        provider, minimum, maximum, key = _selection(_services)
    except ValueError:
        return _services.jsonify({"error": "Provedor ou duração inválidos."}), 400
    job_id = str(_services.request.args.get("job_id", "")).strip()
    if job_id:
        result, status = _services.task_catalog.poll(job_id, key=key, scope="provider-index")
        return _services.jsonify(result), status
    existing = _services.task_catalog.lookup(key, scope="provider-index")
    if existing is not None:
        result, status = existing
        return _services.jsonify(result), status
    range_key = hashlib.sha256(f"{float(minimum)}|{float(maximum)}".encode("ascii")).hexdigest()[:16]
    base_range_key = hashlib.sha256(b"60.0|1800.0").hexdigest()[:16]
    indexes = {"ego4d": (_services.config.DATA_DIR / "task_rank_cache.pkl").is_file(),
               "nymeria": any((_services.campaign.nymeria.data_root() / "_catalog" /
                           f"task_selection_{key}.json").is_file()
                           for key in (range_key, base_range_key))}
    return _services.jsonify({"state": "idle", "indexes_found": indexes,
                             "message": "Preparar baixa as anotações necessárias, verifica as fontes e reutiliza os índices atuais. Vídeos e sensores são adquiridos durante a campanha."})


class _SourcePreparationError(ValueError):
    pass


def _prepare_sources(services, provider, progress):
    """Use each provider's existing acquisition path before cold indexing."""
    if provider == "ego4d":
        progress("Ego4D: verificando e obtendo as anotações necessárias…", phase="ego4d_source_sync")
        services.campaign.ego4d.ensure_task_annotations()
        if not services.campaign._local_narration_catalog():
            raise _SourcePreparationError(
                "Ego4D sem anotações locais. Confira o acesso do provedor em Integrações e prepare novamente.")
        return {"state": "ready"}

    progress("NymeriaPlus: verificando o manifesto e obtendo metadados/anotações…",
             phase="nymeria_source_sync")
    def source_progress(message):
        # The library's callback is a public text callback. Keep its measured
        # sequence count without passing Flask state into download workers.
        counts = re.fullmatch(r"Catálogo Nymeria: (\d+)/(\d+) sequências", message)
        measured = ({"completed": int(counts[1]), "total": int(counts[2])} if counts else {})
        progress(message, phase="nymeria_source_sync", **measured)
    result = services.nymeria_library.sync_catalog(progress=source_progress)
    sync = result.get("sync", {})
    states = list(sync.get("states", {}).values())
    invalid_annotations = states.count("invalid_annotations")
    download_failed = states.count("download_failed")
    errors = len(sync.get("errors", {})) + invalid_annotations
    cataloged = states.count("cataloged")
    if not cataloged:
        raise _SourcePreparationError(
            "NymeriaPlus sem anotações utilizáveis. Confira o manifesto e o acesso aos arquivos na Biblioteca; nenhum índice foi declarado pronto.")
    return {"state": "partial" if errors else "ready", "completed": sync.get("completed", 0),
            "total": sync.get("total", 0), "cataloged": cataloged, "errors": errors,
            "no_annotations": states.count("no_annotations"),
            "invalid_annotations": invalid_annotations, "download_failed": download_failed}


def prepare_provider_catalog(_services):
    try:
        provider, minimum, maximum, key = _selection(_services)
    except ValueError:
        return _services.jsonify({"error": "Provedor ou duração inválidos."}), 400
    campaign = _services.campaign
    def work(progress):
        names = list(campaign.task_matching.TASK_RULES)
        providers = ["ego4d", "nymeria"] if provider == "ambos" else [provider]
        result = []
        for selected in providers:
            source = None
            phase = "sources"
            try:
                source = _prepare_sources(_services, selected, progress)
                phase = "index"
                if not names:
                    raise RuntimeError("No catalog rules configured")
                # Source acquisition must finish before the selection snapshot
                # binds file contents. Otherwise a just-downloaded annotation
                # can remain 'missing' for the whole indexing operation.
                with campaign.ego4d.selection_operation(fresh=True), persist_catalog_indexes():
                    # Prepare the base range as well: category duration hints
                    # compare the selected range against this same full index.
                    for lower, upper in dict.fromkeys(((60.0, 1800.0), (minimum, maximum))):
                        campaign._compatible_task_clips(names[0], selected,
                            min_dur_s=lower, max_dur_s=upper, catalog_only=True)
                    candidates = 0
                    categories = 0
                    seconds = 0.0
                    for index, name in enumerate(names):
                        report_progress(f"Conferindo índice {selected}: {index}/{len(names)} regras",
                            phase="provider_summary", completed=index, total=len(names))
                        clips = campaign._compatible_task_clips(name, selected,
                            min_dur_s=minimum, max_dur_s=maximum, catalog_only=True)
                        accepted = [clip for clip in clips if campaign._catalog_queue_accepts(clip, name)]
                        candidates += len(accepted)
                        categories += bool(accepted)
                        seconds += sum(clip["dur_s"] for clip in accepted)
                    result.append({"provider": selected, "candidates": candidates,
                        "rules_with_candidates": categories, "rules_checked": len(names),
                        "annotation_hours_upper_bound": round(seconds / 3600, 3),
                        "sources": source, "state": "ready" if candidates else "empty"})
                    report_progress(f"Índice {selected}: {len(names)}/{len(names)} regras conferidas",
                        phase="provider_summary", completed=len(names), total=len(names))
                if source["state"] == "partial":
                    result[-1].update(state="partial", error=
                        "Parte das anotações NymeriaPlus não pôde ser obtida ou validada. O conteúdo já preparado foi preservado; confira o manifesto e tente novamente.")
            except Exception as exc:
                # Download errors may contain signed URLs or credentials. Expose
                # our stage-specific diagnostic, never an external exception.
                message = (str(exc) if isinstance(exc, _SourcePreparationError) else
                    (f"Não foi possível obter as anotações de {selected}. Confira a configuração do provedor, a conexão e o espaço livre." if phase == "sources" else
                     f"Não foi possível preparar ou salvar o índice de {selected}. Confira as anotações e o espaço livre; o conteúdo anterior foi preservado."))
                result.append({"provider": selected, "state": "error", "phase": phase,
                               "sources": source, "error": message})
        failed = [item for item in result if item["state"] in {"partial", "error"}]
        candidates = sum(item.get("candidates", 0) for item in result)
        ready = not failed and candidates > 0
        message = ("Anotações e índices preparados. A campanha verificará as categorias atuais da conta e a capacidade sem repetições. Vídeos e sensores serão adquiridos e medidos no preparo de cada envio."
                   if ready else (" ".join(item["error"] for item in failed) if failed else
                   "As anotações foram verificadas, mas não há candidatos compatíveis com esta faixa de duração. Ajuste a faixa ou o provedor antes de iniciar a campanha."))
        body = {"ready": ready, "state": "ready" if ready else "partial" if candidates else "error",
                "providers": result, "min_dur_s": minimum, "max_dur_s": maximum,
                "media_downloaded": False, "message": message}
        if not ready:
            body.update(error=message, code="provider_catalog_incomplete" if failed else "provider_catalog_empty")
        return body, 200 if ready else 409
    # Publish the queued job before releasing the same admission lock used by
    # campaigns and source-library commands. Neither admission order may begin
    # acquisition against a source snapshot that is currently being rebuilt.
    with _services._HEAVY_RUNNER_LOCK:
        if _services.campaign_drain["requested"]:
            return _services.campaign_closing_response()
        if (any(runner.running for runner in (_services.RUNNER, _services.RECOVERY,
                _services.HOLO_CACHE_RUNNER, _services.nymeria_library_worker))
                or _services.campaign_drain["requests"] or _services.campaign_verifications.busy
                or _services.nymeria_catalog.busy):
            return _services.jsonify({"ready": False, "code": "provider_catalog_busy",
                "error": "Nenhuma preparação iniciada. Aguarde a campanha, verificação ou preparação de fontes atual terminar."}), 409
        result, status = _services.task_catalog.get(key, work, scope="provider-index", refresh=True)
    if status == 202 and not result.get("job_id"):
        # A saturated loader reports waiting without admitting a worker. GET
        # observers deliberately cannot enqueue it later on the user's behalf.
        return _services.jsonify({"ready": False, "code": "provider_catalog_busy",
            "error": "Nenhuma preparação iniciada. Aguarde as consultas de catálogo atuais terminarem e tente novamente."}), 409
    return _services.jsonify(result), status
