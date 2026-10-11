"""Explicit provider indexing outside campaign/account admission."""
import hashlib

from ...background_work import report_progress
from ...catalog_policy import persist_catalog_indexes

REQUIRES = ("campaign", "task_catalog", "request", "jsonify", "config", "_parse_duration_range")


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
    range_key = hashlib.sha256(f"{float(minimum)}|{float(maximum)}".encode("ascii")).hexdigest()[:16]
    indexes = {"ego4d": (_services.config.DATA_DIR / "task_rank_cache.pkl").is_file(),
               "nymeria": (_services.campaign.nymeria.data_root() / "_catalog" /
                           f"task_selection_{range_key}.json").is_file()}
    return _services.jsonify({"state": "idle", "indexes_found": indexes,
                             "message": "Preparar verifica as fontes e reutiliza índices que ainda correspondem às anotações atuais."})


def prepare_provider_catalog(_services):
    try:
        provider, minimum, maximum, key = _selection(_services)
    except ValueError:
        return _services.jsonify({"error": "Provedor ou duração inválidos."}), 400
    campaign = _services.campaign
    def work(progress):
        try:
            names = list(campaign.task_matching.TASK_RULES)
            providers = ["ego4d", "nymeria"] if provider == "ambos" else [provider]
            result = []
            with campaign.ego4d.selection_operation(), persist_catalog_indexes():
                for selected in providers:
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
                        "annotation_hours_upper_bound": round(seconds / 3600, 3)})
            return {"ready": True, "providers": result, "min_dur_s": minimum,
                    "max_dur_s": maximum, "media_downloaded": False,
                    "message": "Índices preparados. A campanha verificará as categorias atuais da conta e a capacidade sem repetições. Sensores e mídia serão medidos no preparo de cada envio."}, 200
        except (OSError, ValueError, RuntimeError, ImportError):
            return {"error": "Não foi possível preparar os índices. Confira as anotações Ego4D em Integrações e o manifesto/anotações NymeriaPlus no Acelerador; depois tente novamente."}, 400
    result, status = _services.task_catalog.get(key, work, scope="provider-index", refresh=True)
    return _services.jsonify(result), status
