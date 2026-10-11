"""catalogs routes: existing behavior with explicit dependencies."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any
from ...catalog_policy import prepared_catalog_read, CatalogPreparationRequired
from contextlib import nullcontext

REQUIRES = ('AuthError', 'ORG_MIGRATION', 'Session', '_ACCOUNT_OPERATION_LOCK', '_BULK_REGISTER_STATE', '_catalog_account_failure', '_catalog_ban_record_confirms_exclusion', '_catalog_identity_fingerprint', '_identity_route', '_local_dataset_provider', '_parse_duration_range', '_resolve_org', 'account_issue', 'app', 'campaign', 'json', 'jsonify', 'request', 'task_catalog')


def get_tasks(_services):
    email = str(_services.request.args.get("email", "")).strip()
    if not email:
        return _services.jsonify({"error": "informe ?email=<conta>"}), 400
    try:
        dataset_provider = _services._local_dataset_provider(
            _services.request.args.get("dataset")
        )
        content_mode = _services.campaign.normalize_content_mode(
            _services.request.args.get("content_mode")
        )
    except ValueError:
        return _services.jsonify({"error": "Dataset ou modo de conteúdo inválido.",
                        "code": "invalid_task_selection"}), 400
    try:
        min_dur_s, max_dur_s = _services._parse_duration_range(_services.request.args)
    except ValueError:
        return _services.jsonify({"error": "Informe um intervalo de duração válido.",
                        "code": "invalid_task_duration"}), 400
    if _services.request.args.get("async") == "1":
        prepared_only = _services.request.args.get("prepared_only") == "1"
        key = (email, dataset_provider, content_mode, min_dur_s, max_dur_s,
               _services._catalog_identity_fingerprint([email]) + ("|prepared" if prepared_only else ""))
        job_id = str(_services.request.args.get("job_id", "")).strip()
        if job_id:
            result, status = _services.task_catalog.poll(job_id, key=key, scope="campaign-tasks")
            if status == 409 and _services._catalog_ban_record_confirms_exclusion(email):
                diagnostic = _services.task_catalog.poll_account_exclusion_diagnostic(
                    job_id, key_prefix=key[:5], scope="campaign-tasks")
                if diagnostic is not None:
                    result, status = diagnostic
            return _services.jsonify(result), status

        def load(progress):
            progress("Conferindo acesso e categorias da conta…")
            with _services._ACCOUNT_OPERATION_LOCK:
                if _services.ORG_MIGRATION.running or _services._BULK_REGISTER_STATE.get("state") == "running":
                    return {"error": "Aguarde a operação de contas em andamento."}, 409
                try:
                    with _services._identity_route(email):
                        sess = _services.Session.from_email(email)
                        org_key = _services._resolve_org(email, session=sess)
                        remote_tasks = sess.all_tasks(org_key)
                except Exception as exc:
                    return _services._catalog_account_failure(email, exc)
            # Local indexing does not hold account authentication or
            # mutation locks. Polling requests never wait on this work.
            try:
                progress("Preparando catálogo para a duração e o conteúdo selecionados…")
                with prepared_catalog_read() if prepared_only else nullcontext():
                    tasks = _services.campaign.available_tasks(
                        email, org_key, min_dur_s=min_dur_s, max_dur_s=max_dur_s,
                        include_unavailable=True, dataset_provider=dataset_provider,
                        content_mode=content_mode, remote_tasks=remote_tasks)
                return {"email": email, "org_key": org_key, "tasks": tasks,
                        "dataset": dataset_provider, "content_mode": content_mode,
                        "scenarios_pt": _services.campaign.SCENARIO_PT}, 200
            except CatalogPreparationRequired as exc:
                return {"error": str(exc), "code": "provider_catalog_preparation_required"}, 409
            except Exception as exc:
                issue = _services.account_issue(email, exc, stage="Preparação local do catálogo")
                return {"error": issue["reason"] + " " + issue["action"], "issue": issue}, 400

        result, status = _services.task_catalog.get(
            key, load, scope="campaign-tasks",
            refresh=_services.request.args.get("refresh") == "1")
        return _services.jsonify(result), status
    try:
        # Uma Session só: resolve a organização e consulta o acesso remoto
        # dentro da rota da conta; o preparo local fica fora dela.
        # The synchronous endpoint already owns _ACCOUNT_OPERATION_LOCK
        # through before_request; acquiring it again here would deadlock.
        with _services._identity_route(email):
            sess = _services.Session.from_email(email)
            org_key = _services._resolve_org(email, session=sess)
            remote_tasks = sess.all_tasks(org_key)
    except _services.json.JSONDecodeError:
        _services.app.logger.warning("GET /api/tasks: catalog_account_unavailable")
        result, status = _services._catalog_account_failure(email, _services.AuthError(
            "Resposta remota inválida.", code="invalid_response"))
        return _services.jsonify(result), status
    except _services.AuthError as exc:
        # Keep account identity in the typed response issue, never in logs.
        _services.app.logger.warning("GET /api/tasks: catalog_account_unavailable")
        result, status = _services._catalog_account_failure(email, exc)
        return _services.jsonify(result), status
    except (RuntimeError, OSError, ValueError):
        _services.app.logger.warning("GET /api/tasks: task_catalog_unavailable")
        return _services.jsonify({"error": "Não foi possível carregar as categorias. Confira o acesso da conta e a biblioteca local.",
                        "code": "task_catalog_unavailable"}), 400
    try:
        tasks = _services.campaign.available_tasks(
            email, org_key, min_dur_s=min_dur_s, max_dur_s=max_dur_s,
            include_unavailable=True, dataset_provider=dataset_provider,
            content_mode=content_mode, remote_tasks=remote_tasks)
    except _services.json.JSONDecodeError:
        return _services.jsonify({
            "error": "a API devolveu resposta vazia (não-JSON). Tente de novo.",
            "code": "task_response_invalid",
        }), 400
    except (RuntimeError, OSError, ValueError):
        _services.app.logger.warning("GET /api/tasks: task_catalog_unavailable")
        return _services.jsonify({"error": "Não foi possível carregar as categorias. Confira o acesso da conta e a biblioteca local.",
                        "code": "task_catalog_unavailable"}), 400
    return _services.jsonify({"email": email, "org_key": org_key,
                    "dataset": dataset_provider, "content_mode": content_mode,
                    "tasks": tasks,
                    "scenarios_pt": _services.campaign.SCENARIO_PT})
