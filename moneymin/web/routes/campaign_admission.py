"""campaign admission routes: existing behavior with explicit dependencies."""
from __future__ import annotations
from contextlib import contextmanager, nullcontext
from typing import Any
from ...catalog_policy import CatalogPreparationRequired, prepared_catalog_read

REQUIRES = ('AccountSpec', 'AuthError', 'BALANCES_RUNNER', 'CampaignConfig', 'HOLO_CACHE_RUNNER', 'OperationLeaseError', 'RECOVERY', 'RUNNER', 'Session', 'TaskSpec', 'ThreadPoolExecutor', '_HEAVY_RUNNER_LOCK', '_ban_accounts', '_identity_route', '_list_accounts', '_local_dataset_provider', '_parse_active_hours', '_parse_duration_range', '_preflight_fingerprint', '_resolve_org', '_storage_snapshot', '_validate_campaign_request', 'account_issue', 'as_completed', 'background_campaign_preflight', 'banned_store', 'campaign', 'campaign_closing_response', 'campaign_drain', 'campaign_start_store', 'campaign_verifications', 'config', 'hashlib', 'issue_text', 'json', 'jsonify', 'math', 'nymeria_library_worker', 'preflight_lock', 'preflights', 're', 'readiness', 'recovery', 'request', 'run_campaign_preflight', 'time', 'uuid', 'validate_task_catalog')


def campaign_preflight(_services):
    """Valida a operação inteira sem baixar, preparar ou enviar mídia."""
    body = _services.request.get_json(silent=True)
    if body is None:
        body = {}
    try:
        _services._validate_campaign_request(body)
    except (ValueError, TypeError, OverflowError) as exc:
        return _services.jsonify({"error": f"parâmetros inválidos: {exc}"}), 400
    if _services.request.args.get("async") == "1":
        request_id = _services.request.args.get("request_id", "")
        if not _services.re.fullmatch(r"[a-f0-9]{32}", request_id):
            return _services.jsonify({"error": "Identificador de verificação inválido."}), 400
        body_digest = _services.hashlib.sha256(_services.json.dumps(body, sort_keys=True).encode()).hexdigest()
        result, status = _services.campaign_verifications.get(
            (request_id, body_digest), lambda progress: _services.background_campaign_preflight(body, progress))
    else:
        # Sync consumers share the same drain counter as async so close_drain
        # stays blocked while this preflight is still running.
        result, status = _services.background_campaign_preflight(body, lambda _message: None)
    return _services.jsonify(result), status

def background_campaign_preflight(_services, body, progress):
    # Updates and shutdown must also wait for authentication/token writes
    # performed by the worker after its initiating HTTP request has ended.
    with _services._HEAVY_RUNNER_LOCK:
        if _services.campaign_drain["requested"]:
            return {"error_code": "campaign_closing", "error": "O aplicativo está encerrando a operação."}, 409
        _services.campaign_drain["requests"] += 1
    try:
        return _services.run_campaign_preflight(body, progress)
    finally:
        with _services._HEAVY_RUNNER_LOCK:
            _services.campaign_drain["requests"] -= 1

def run_campaign_preflight(_services, body, progress=lambda message: None):
    """Same admission checks for legacy callers and background verification."""
    blockers: list[str] = []
    warnings: list[str] = []
    if _services.RUNNER.running:
        blockers.append("já existe uma campanha em andamento")
    if _services.HOLO_CACHE_RUNNER.running:
        blockers.append("o acelerador está em execução")
    try:
        provider = _services._local_dataset_provider(body.get("dataset"))
        content_mode = _services.campaign.normalize_content_mode(body.get("content_mode"))
        requested_workers = body.get("account_workers", _services.campaign.max_account_workers())
        if (isinstance(requested_workers, bool) or not isinstance(requested_workers, int)
                or not 1 <= requested_workers <= 15):
            raise ValueError("envios simultâneos devem estar entre 1 e 15")
        min_dur_s, max_dur_s = _services._parse_duration_range(body)
        count = max(1, min(int(body.get("count", 1)), 200))
        target_hours = max(0.0, min(float(body.get("target_hours") or 0), 12.0))
        until_exhausted = body.get("run_until_exhausted", True)
        if until_exhausted:
            target_hours = 0.0
    except (TypeError, ValueError, OverflowError) as exc:
        return {"error": f"parâmetros inválidos: {exc}"}, 400

    emails = [str(e).strip() for e in body.get("accounts", []) if str(e).strip()]
    raw_tasks = body.get("tasks", [])
    if not emails:
        blockers.append("selecione ao menos uma conta")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        blockers.append("selecione ao menos uma categoria")
    try:
        known = {account["email"] for account in _services._list_accounts()}
    except _services.banned_store.BannedStoreError:
        raise
    except ValueError:
        return {"error": "Registro de contas inválido. Restaure os dados antes de continuar."}, 400
    missing_accounts = [email for email in emails if email not in known]
    accounts: list[_services.AccountSpec] = []
    account_errors: list[str] = []
    account_issues: list[dict[str, Any]] = []
    for email in missing_accounts:
        account_issues.append(_services.account_issue(email, _services.AuthError("sem token salvo")))
    if emails:
        progress("Conferindo acesso e organização das contas…")
        resolved: list[_services.AccountSpec | None] = [None] * len(emails)
        with _services.ThreadPoolExecutor(max_workers=max(1, min(6, len(emails)))) as pool:
            futures = {pool.submit(_services._resolve_org, email): i
                       for i, email in enumerate(emails) if email in known}
            for future in _services.as_completed(futures):
                index = futures[future]
                try:
                    resolved[index] = _services.AccountSpec(emails[index], future.result())
                except (_services.AuthError, RuntimeError, OSError, ValueError) as exc:
                    account_issues.append(_services.account_issue(emails[index], exc))
        accounts = [account for account in resolved if account is not None]

    selected: list[dict[str, Any]] = []
    unavailable: list[str] = []
    catalog_loaded = False
    catalog_error = None
    if accounts and raw_tasks:
        progress("Conferindo categorias e conteúdo selecionado…")
        catalog = []
        catalog_owner = None
        for account in accounts:
            try:
                with _services._identity_route(account.email):
                    with prepared_catalog_read() if content_mode == "dataset" else nullcontext():
                        catalog = _services.campaign.available_tasks(
                            account.email, account.org_key,
                            min_dur_s=min_dur_s, max_dur_s=max_dur_s,
                            include_unavailable=True, dataset_provider=provider,
                            content_mode=content_mode)
                if not isinstance(catalog, list) or any(not isinstance(item, dict) for item in catalog):
                    raise RuntimeError("resposta de categorias inválida")
                catalog_loaded = True
                catalog_owner = account.email
                break
            except CatalogPreparationRequired as exc:
                # A local index is shared by the verified accounts; retrying
                # another owner cannot prepare it and is not an account issue.
                catalog_error = str(exc)
                blockers.append(catalog_error)
                break
            except (_services.AuthError, RuntimeError, OSError, ValueError, _services.json.JSONDecodeError) as exc:
                issue = _services.account_issue(account.email, exc, stage="Carregamento das categorias")
                account_issues.append(issue)
                catalog = []
                # Try the other verified accounts; preserve each failure as
                # a diagnostic and exclude its owner only from this run.
        by_id = {str(item.get("id")): item for item in catalog if item.get("id")}
        seen: set[str] = set()
        for raw in raw_tasks if catalog_loaded else []:
            task_id = str(raw.get("task_id", "")).strip() if isinstance(raw, dict) else ""
            item = by_id.get(task_id)
            if not task_id or item is None:
                blockers.append("uma categoria selecionada não está mais disponível")
                continue
            if task_id in seen:
                continue
            seen.add(task_id)
            label = str(item.get("name_pt") or item.get("name") or task_id)
            if item.get("available_for_duration") is False:
                unavailable.append(label)
                continue
            selected.append(item)

        selected_ids = {str(item.get("id")) for item in selected}
        def _check_account_tasks(account: AccountSpec) -> tuple[str, set[str] | Exception | str]:
            try:
                with _services._identity_route(account.email):
                    sess = _services.Session.from_email(account.email)
                    sess.ensure_auth()
                    account_tasks = sess.all_tasks(account.org_key)
                account_tasks = _services.validate_task_catalog(account_tasks)
                audio_by_id = {str(item.get("id")): item.get("requiresAudio", False)
                               for item in account_tasks}
                if any(audio_by_id.get(str(item["id"]), False)
                       != (item.get("requires_audio") is True) for item in selected
                       if str(item["id"]) in audio_by_id):
                    return account.email, "As contas possuem exigências de áudio diferentes. Selecione contas com o mesmo catálogo."
                account_ids = {
                    str(item.get("id")) for item in account_tasks
                    if item.get("id")
                }
            except (_services.AuthError, RuntimeError, OSError, ValueError) as exc:
                return account.email, exc
            return account.email, selected_ids - account_ids

        restricted = {item["email"] for item in account_issues}
        others = ([account for account in accounts
                   if account.email != catalog_owner and account.email not in restricted]
                  if catalog_loaded else [])
        progress("Conferindo categorias nas demais contas…")
        with _services.ThreadPoolExecutor(max_workers=max(1, min(4, len(others)))) as pool:
            checks = pool.map(_check_account_tasks, others)
            for email, result in checks:
                if isinstance(result, str):
                    blockers.append(f"{email}: {result}")
                    continue
                if isinstance(result, Exception):
                    account_issues.append(_services.account_issue(
                        email, result, stage="Consulta das categorias"))
                    continue
                if result:
                    blockers.append(
                        f"{email} não possui {len(result)} categoria(s) selecionada(s)"
                    )

    if unavailable:
        warnings.append(f"{len(unavailable)} categoria(s) sem clipe na duração escolhida")
    if raw_tasks and catalog_loaded and not selected:
        blockers.append("nenhuma categoria selecionada possui clipe compatível")

    account_issues.sort(key=lambda item: (emails.index(item["email"]), item["stage"]))
    # Restrição confirmada sai da seleção na hora. Timeout, senha e rede
    # continuam pendências e não autorizam arquivo na lista de banidas.
    confirmed = [item for item in account_issues if item.get("restriction_confirmed") is True]
    removed_now: list[str] = []
    if confirmed:
        try:
            _services._ban_accounts(confirmed)
        except (OSError, ValueError):
            blockers.append(
                "Não foi possível mover as contas restritas para Banidas. "
                "A campanha não foi alterada.")
        else:
            removed_now = [str(item["email"]) for item in confirmed]
            removed_emails = set(removed_now)
            accounts = [account for account in accounts if account.email not in removed_emails]
            account_issues = [
                item for item in account_issues if item.get("restriction_confirmed") is not True]
            listed = ", ".join(removed_now)
            warnings.append(
                f"{listed} foi movida para Banidas e não entra nesta campanha."
                if len(removed_now) == 1 else
                f"{len(removed_now)} contas com restrição confirmada foram movidas "
                f"para Banidas e não entram nesta campanha: {listed}.")
    account_errors = [_services.issue_text(item) for item in account_issues]
    # Compatibilidade: clientes antigos leem apenas blockers. Não esconder
    # a conta nem classificar falhas de rede como credenciais inválidas.
    blockers.extend(account_errors)
    if emails and not accounts and not account_issues:
        blockers.append(
            "Nenhuma conta selecionada permanece disponível. "
            "As contas com restrição confirmada foram movidas para Banidas.")

    clip_count = sum(int(item.get("clip_count") or 0) for item in selected)
    if target_hours > 0:
        estimated_sends = 0  # The reviewed pool determines this estimate.
    else:
        estimated_sends = len(selected) * count * len(accounts)
    progress("Conferindo requisitos da instalação e pendências de envio…")
    try:
        ready = _services.readiness.campaign_readiness(provider, content_mode=content_mode)
    except Exception:  # Os outros checks continuam sem exportar dados da exceção.
        ready = {"ready": False, "checks": [], "error": "Não foi possível verificar a prontidão local.",
                 "code": "readiness_unavailable"}
    readiness_errors = [
        item for item in ready.get("checks", [])
        if item.get("status") == "error"
    ]
    for item in readiness_errors:
        message = f"{item.get('name')}: {item.get('detail')}"
        if message not in blockers:
            blockers.append(message)
    if not ready.get("ready") and not readiness_errors and ready.get("error"):
        blockers.append(f"prontidão indisponível: {ready['error']}")
    storage = _services._storage_snapshot(include_path=False)
    if storage.get("free_bytes", 0) < 10 * 1024 ** 3:
        warnings.append("há menos de 10 GiB livres na unidade da biblioteca")

    removable = {i["email"] for i in account_issues if i.get("restriction_confirmed") is True}
    skipped_accounts = {i["email"] for i in account_issues if i.get("restriction_confirmed") is not True}
    survivors = [a for a in accounts if a.email not in removable | skipped_accounts]
    from ...delivery.admission import campaign_create_blocker
    try:
        create_blocker = campaign_create_blocker(survivors)
    except (ValueError, OSError, RuntimeError):
        create_blocker = "Não foi possível ler os recibos de CREATE. Os registros foram preservados; tente iniciar novamente após restabelecer a leitura local."
    if create_blocker:
        blockers.append(create_blocker)
    if target_hours <= 0:
        estimated_sends = len(selected) * count * len(survivors)
    recovery_exclusions = {}
    recovery_error = None
    try:
        unresolved = _services.recovery.snapshot()["items"]
        survivor_emails = {account.email for account in survivors}
        selected_recovery = [item for item in unresolved if item["email"] in survivor_emails]
        unidentified = sorted({item["email"] for item in selected_recovery if item.get("blocks_campaign", True)})
        if unidentified:
            blockers.append("Pendências sem clipe identificado em: " + ", ".join(unidentified)
                            + ". Não foi possível identificar o conteúdo já reservado; os registros foram preservados.")
        recovery_exclusions = _services.recovery.campaign_exclusions(selected_recovery)
        if recovery_exclusions:
            warnings.append(f"{len(recovery_exclusions)} clipe(s) reservado(s) por envios anteriores. "
                            "Esses clipes não serão reenviados nem contarão como progresso novo; a campanha usará outros conteúdos.")
    except (ValueError, OSError) as exc:
        from ...recovery_errors import error_response
        failure = error_response(exc)
        recovery_error = failure["recovery_error"]
        blockers.append(failure["error"])
    reusable = (bool(survivors) and bool(selected) and catalog_loaded and ready.get("ready") is True
                and len(blockers) == len(account_errors))
    receipt_id = None
    candidate_plan = None
    clip_review = []
    sent_fingerprint = None
    capacity_summary = None
    if reusable and (body.get("include_clip_plan") is True or target_hours > 0):
        progress("Preparando a prévia dos clipes…")
        from ... import campaign_plan
        review_tasks = [_services.TaskSpec(
            task_id=str(item["id"]), scenario=str(item["scenario"]),
            task_name=str(item.get("name") or item["scenario"]),
            task_label=str(item.get("name_pt") or item.get("name") or item["scenario"]),
            requires_audio=item.get("requires_audio") is True,
            min_dur_s=min_dur_s, max_dur_s=max_dur_s, count=count,
        ) for item in selected]
        try:
            with prepared_catalog_read() if content_mode == "dataset" else nullcontext():
                candidate_plan, clip_review, sent_fingerprint = campaign_plan.build(_services.CampaignConfig(
                    accounts=survivors, tasks=review_tasks, dataset_provider=provider,
                    content_mode=content_mode, recovery_exclusions=recovery_exclusions), catalog_only=True)
            clip_count = len(clip_review)
            capacity_summary = campaign_plan.capacity(
                clip_review, [a.email for a in survivors], target_seconds=target_hours * 3600,
                count_per_task=None if until_exhausted or target_hours > 0 else count)
            estimated_sends = capacity_summary["estimated_sends"]
            refined_count = sum(bool(row.get("imu_refined_from")) for row in clip_review)
            if refined_count:
                warnings.append(f"{refined_count} trecho(s) recortado(s) para evitar lacunas nos sensores. "
                                "A prévia já usa essas durações; partes de vídeos já recebidos continuam excluídas.")
            if target_hours > 0:
                if not capacity_summary["can_reach_goal"]:
                    blockers.append(campaign_plan.capacity_error(capacity_summary))
                    reusable = False
            if not any(row["eligible_accounts"] for row in clip_review):
                blockers.append("Nenhum clipe candidato está disponível para as contas selecionadas. Revise o conteúdo e a lista de vídeos usados.")
                reusable = False
        except CatalogPreparationRequired as exc:
            catalog_error = str(exc)
            blockers.append(catalog_error)
            reusable = False
        except Exception:
            blockers.append("Não foi possível preparar a prévia dos clipes. Recarregue o catálogo e tente novamente.")
            reusable = False
    if reusable:
        with _services.preflight_lock:
            now = _services.time.monotonic()
            for key in list(_services.preflights):
                if _services.preflights[key]["expires"] <= now:
                    _services.preflights.pop(key)
            if len(_services.preflights) >= 32:
                _services.preflights.pop(next(iter(_services.preflights)))
            receipt_id = _services.uuid.uuid4().hex
            _services.preflights[receipt_id] = {"body": body, "accounts": survivors, "catalog": catalog,
                                      "candidate_plan": candidate_plan, "sent_fingerprint": sent_fingerprint,
                                      "issues": [i for i in account_issues if i.get("restriction_confirmed") is True],
                                      "skipped_accounts": sorted(skipped_accounts), "expires": now + 600,
                                      "fingerprint_emails": [a.email for a in survivors],
                                      "fingerprint": _services._preflight_fingerprint([a.email for a in survivors])}

    return {
        "ok": not blockers,
        "preflight_id": receipt_id,
        "clip_plan": clip_review,
        "can_remove_and_continue": reusable and bool(removable),
        "can_skip_and_continue": reusable and bool(skipped_accounts),
        "skippable_accounts": sorted(skipped_accounts),
        "removable_accounts": sorted(removable),
        "removed_accounts": removed_now,
        "provider": provider,
        "accounts": {"selected": len(emails), "validated": len(survivors)},
        "tasks": {"selected": len(raw_tasks), "compatible": len(selected)},
        "clips": clip_count,
        "estimated_sends": estimated_sends,
        "account_workers": _services.campaign.clamp_account_workers(requested_workers, len(survivors)),
        "target_hours": target_hours,
        "run_until_exhausted": until_exhausted,
        "capacity": capacity_summary,
        "blockers": blockers,
        "recovery_error": recovery_error,
        "warnings": warnings,
        "account_errors": account_errors,
        "account_issues": account_issues,
        "readiness": ready,
        "storage": storage,
        **({"code": "provider_catalog_preparation_required", "error": catalog_error}
           if catalog_error else {}),
    }, 409 if catalog_error else 200

def start_campaign(_services):
    # Legacy callers have no durable correlation key, but a broken
    # authoritative ledger still cannot authorize a fresh operation.
    try:
        _services.campaign_start_store.lookup('local-state-integrity-check')
    except (ValueError,OSError):
        return _services.jsonify({"error_code":"start_state_unreadable","error":"Preserve o registro local de início para revisão."}),409
    retry_body = _services.request.get_json(silent=True)
    if isinstance(retry_body, dict) and retry_body.get("preflight_id"):
        retry_id = retry_body["preflight_id"]
        frozen_request = {k:v for k,v in retry_body.items() if k not in {"preflight_id", "remove_restricted", "skip_unverified"}}
        try:
            prior = _services.campaign_start_store.lookup(retry_id, kind="dataset", receipt_id=retry_id, body=frozen_request)
        except _services.campaign_start_store.StartConflictError:
            return _services.jsonify({"error_code":"start_request_conflict","error":"O identificador de início pertence a outra operação."}),409
        except (ValueError,OSError):
            return _services.jsonify({"error_code":"start_state_unreadable","error":"Preserve o registro local de início para revisão."}),409
        if prior is not None:
            if prior["phase"] == "claimed":
                return _services.jsonify({"error_code":"start_outcome_unknown","error":"O início exige consulta e revisão; não reenvie a operação."}),500
            return _services.jsonify({**prior["reply"],"already_running":True})
    if _services.RUNNER.running:
        return _services.jsonify({
            "ok": True,
            "already_running": True,
            "state": _services.RUNNER.state,
            "total_sends": _services.RUNNER.total_sends,
        })
    if _services.HOLO_CACHE_RUNNER.running:
        return _services.jsonify({
            "error": "pare o acelerador antes de iniciar a campanha",
        }), 409
    body = _services.request.get_json(silent=True)
    if body is None:
        body = {}
    try:
        _services._validate_campaign_request(body)
    except (ValueError, TypeError, OverflowError) as exc:
        return _services.jsonify({"error": f"parâmetros inválidos: {exc}"}), 400
    receipt = None
    receipt_id = body.get("preflight_id")
    if receipt_id:
        with _services.preflight_lock:
            receipt = _services.preflights.get(str(receipt_id))
        original = {k: v for k, v in body.items() if k not in {"preflight_id", "remove_restricted", "skip_unverified"}}
        invalid = None
        if receipt is None:
            invalid = ("preflight_missing", "A prévia não está mais disponível. Revise a campanha novamente.")
        elif receipt["expires"] <= _services.time.monotonic():
            invalid = ("preflight_expired", "A prévia passou de 10 minutos. Atualize e revise antes de iniciar.")
        elif original != receipt["body"]:
            invalid = ("preflight_request_changed", "Os parâmetros da campanha mudaram. Revise a nova prévia.")
        elif receipt["fingerprint"] != _services._preflight_fingerprint(receipt["fingerprint_emails"]):
            invalid = ("preflight_accounts_changed", "O acesso ou a identidade de uma conta mudou. Revise a nova verificação.")
        if invalid:
            return _services.jsonify({"error_code": invalid[0], "error": invalid[1]}), 409
        if receipt["issues"] and body.get("remove_restricted") is not True:
            return _services.jsonify({"error": "Confirme a remoção das contas com restrição para continuar."}), 400
        if receipt["skipped_accounts"] and body.get("skip_unverified") is not True:
            return _services.jsonify({"error": "Confirme continuar somente com as contas aprovadas. As demais permanecem cadastradas."}), 400
    elif body.get("remove_restricted") or body.get("skip_unverified"):
        return _services.jsonify({"error": "Execute o preflight antes de remover contas e continuar."}), 400
    cleanup_after_upload = body.get("cleanup_after_upload", True)
    if not isinstance(cleanup_after_upload, bool):
        return _services.jsonify({
            "error": "cleanup_after_upload deve ser true ou false",
        }), 400
    try:
        dataset_provider = _services._local_dataset_provider(
            body.get("dataset")
        )
        content_mode = _services.campaign.normalize_content_mode(body.get("content_mode"))
        requested_workers = body.get("account_workers", _services.campaign.max_account_workers())
        if (isinstance(requested_workers, bool) or not isinstance(requested_workers, int)
                or not 1 <= requested_workers <= 15):
            raise ValueError("envios simultâneos devem estar entre 1 e 15")
    except ValueError as exc:
        return _services.jsonify({"error": str(exc)}), 400
    emails = [str(e).strip() for e in body.get("accounts", []) if str(e).strip()]
    if receipt:
        emails = [a.email for a in receipt["accounts"]]
    raw_tasks = body.get("tasks", [])
    if not emails:
        return _services.jsonify({"error": "selecione ao menos uma conta"}), 400
    if not raw_tasks:
        return _services.jsonify({"error": "selecione ao menos uma categoria"}), 400
    try:
        count = max(1, min(int(body.get("count", 1)), 200))
        if "target_hours" in body:
            target_hours = max(0.0, min(float(body.get("target_hours") or 0), 12.0))
        else:
            target_hours = 0.0
        until_exhausted = body.get("run_until_exhausted", True)
        if until_exhausted:
            target_hours = 0.0
        min_dur_s, max_dur_s = _services._parse_duration_range(body)
        raw_delay_s = float(body.get("delay_s", 0))
        # Campo antigo ainda é validado para clientes desatualizados.
        raw_account_gap_s = float(body.get("account_gap_s", 300))
        if not all(_services.math.isfinite(v) for v in (
                raw_delay_s, raw_account_gap_s, target_hours)):
            raise ValueError("valor não finito")
        delay_s = max(0.0, min(raw_delay_s, 3600.0))
    except (TypeError, ValueError, OverflowError) as exc:
        return _services.jsonify({"error": f"parâmetros numéricos inválidos: {exc}"}), 400
    delay_mode = str(body.get("delay_mode", "off"))
    if delay_mode not in ("off", "clip", "fixed"):
        return _services.jsonify({"error": "delay_mode inválido (off|clip|fixed)"}), 400
    active_hours = _services._parse_active_hours(body.get("active_hours"))
    if active_hours is False:
        return _services.jsonify({"error": "active_hours inválido — use [início, fim] "
                        "com 0 <= início < fim <= 24"}), 400

    try:
        known = {a["email"] for a in _services._list_accounts()}
    except ValueError:
        return _services.jsonify({"error": "Registro de contas banidas inválido. A campanha não iniciou."}), 500
    missing = [e for e in emails if e not in known]
    if missing:
        return _services.jsonify({"error": "conta(s) sem token: " + ", ".join(missing)}), 400

    # O POST público continua seguro mesmo se um cliente antigo pular o
    # preflight. Sem ferramentas, índices ou credenciais da origem, iniciar
    # uma thread apenas produziria uma falha tardia depois do download.
    # Recheck mutable local prerequisites even with an unexpired receipt.
    # The reviewed server-owned catalog and candidate plan stay frozen.
    try:
        environment = _services.readiness.campaign_readiness(
            dataset_provider, content_mode=content_mode)
    except (ValueError, OSError, RuntimeError) as exc:
        return _services.jsonify({"error": f"não foi possível validar a prontidão: {exc}"}), 400
    if not isinstance(environment, dict) or environment.get("ready") is not True:
        return _services.jsonify({"error": "ambiente não está pronto; execute o preflight novamente."}), 400
    environment_errors = [
        item for item in environment.get("checks", [])
        if item.get("status") == "error"
    ]
    if environment_errors:
        details = "; ".join(
            f"{item.get('name')}: {item.get('detail')}"
            for item in environment_errors
        )
        return _services.jsonify({"error": "ambiente não está pronto: " + details}), 400
    if receipt:
        accounts = receipt["accounts"]
        available = receipt["catalog"]
        skipped = []
    else:
        resolved: list[_services.AccountSpec | None] = [None] * len(emails)
        skipped: list[str] = []
        workers = max(1, min(8, len(emails)))
        with _services.ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_services._resolve_org, email): i
                    for i, email in enumerate(emails)}
            for fut in _services.as_completed(futs):
                i = futs[fut]
                try:
                    resolved[i] = _services.AccountSpec(emails[i], fut.result())
                except (_services.AuthError, RuntimeError, OSError) as exc:
                    skipped.append(f"{emails[i]}: {exc}")
        accounts = [acc for acc in resolved if acc is not None]
        if skipped:
            return _services.jsonify({
                "error": "campanha bloqueada: não foi possível validar o acesso "
                         "e a organização de todas as contas. " + "; ".join(skipped),
            }), 400

        # Nunca aceite do browser a associação task_id -> cenário. Uma aba
        # antiga ou uma troca rápida de conta podia mandar um par inconsistente
        # e selecionar vídeos de outra categoria. O catálogo atual da conta é a
        # fonte de verdade.
        try:
            with _services._identity_route(accounts[0].email):
                with prepared_catalog_read() if content_mode == "dataset" else nullcontext():
                    available = _services.campaign.available_tasks(
                        accounts[0].email, accounts[0].org_key,
                        min_dur_s=min_dur_s, max_dur_s=max_dur_s,
                        include_unavailable=True, dataset_provider=dataset_provider,
                        content_mode=content_mode)
            if not isinstance(available, list) or any(not isinstance(item, dict) for item in available):
                raise RuntimeError("resposta de categorias inválida")
        except CatalogPreparationRequired as exc:
            return _services.jsonify({"code": "provider_catalog_preparation_required", "error": str(exc)}), 409
        except _services.json.JSONDecodeError:
            return _services.jsonify({
                "error": "a API devolveu resposta vazia (não-JSON). Tente de novo.",
            }), 400
        except (_services.AuthError, RuntimeError, OSError, ValueError) as exc:
            msg = str(exc)
            if "Expecting value" in msg:
                msg = "a API devolveu resposta vazia (não-JSON). Tente de novo."
            return _services.jsonify({"error": msg}), 400
    by_id = {str(t.get("id")): t for t in available if t.get("id")}

    tasks: list[_services.TaskSpec] = []
    seen_ids: set[str] = set()
    for t in raw_tasks:
        if not isinstance(t, dict):
            return _services.jsonify({"error": "categoria inválida"}), 400
        task_id = str(t.get("task_id", "")).strip()
        authoritative = by_id.get(task_id)
        if not task_id or authoritative is None:
            return _services.jsonify({"error": f"categoria indisponível: {task_id or '(sem id)'}"}), 400
        if task_id in seen_ids:
            continue
        seen_ids.add(task_id)
        # Sem footage compatível (higiene / duração): ignora, não aborta o lote.
        if authoritative.get("available_for_duration") is False:
            skipped.append(str(authoritative.get("name_pt")
                               or authoritative.get("name")
                               or task_id))
            continue
        scenario = str(authoritative["scenario"])
        task_name = str(authoritative.get("name") or scenario)
        task_label = str(authoritative.get("name_pt") or task_name)
        task_description = str(authoritative.get("description") or "")
        tasks.append(_services.TaskSpec(task_id=task_id, scenario=scenario,
                              min_dur_s=min_dur_s, max_dur_s=max_dur_s,
                              task_name=task_name, task_label=task_label,
                              task_description=task_description,
                              requires_audio=authoritative.get("requires_audio") is True,
                              count=count))

    if not tasks:
        return _services.jsonify({
            "error": "nenhuma categoria selecionada tem clipe compatível "
                     "(sentado/celular/título). " + (
                         ("Fora: " + ", ".join(skipped)) if skipped else ""),
        }), 400

    # Preflight estrito nas DEMAIS contas. A primeira já foi autenticada e
    # forneceu o catálogo autoritativo acima; todas as outras precisam ter
    # as mesmas tasks antes de qualquer download/upload começar.
    selected_ids = {task.task_id for task in tasks}

    def _preflight(account: AccountSpec) -> tuple[str, set[str] | str]:
        try:
            with _services._identity_route(account.email):
                sess = _services.Session.from_email(account.email)
                sess.ensure_auth()
                account_tasks = sess.all_tasks(account.org_key)
            account_tasks = _services.validate_task_catalog(account_tasks)
            audio_by_id = {str(task.get("id")): task.get("requiresAudio", False)
                           for task in account_tasks}
            if any(audio_by_id.get(task.task_id, False) != task.requires_audio
                   for task in tasks if task.task_id in audio_by_id):
                raise RuntimeError("As contas possuem exigências de áudio diferentes. Selecione contas com o mesmo catálogo.")
            account_task_ids = {
                str(task.get("id")) for task in account_tasks
                if task.get("id")
            }
        except (_services.AuthError, RuntimeError, OSError, ValueError) as exc:
            return account.email, f"preflight falhou para {account.email}: {exc}"
        missing_tasks = selected_ids - account_task_ids
        return account.email, missing_tasks

    others = [] if receipt else accounts[1:]
    if others:
        by_email = {acc.email: acc for acc in accounts}
        viable = [accounts[0]]
        workers = max(1, min(8, len(others)))
        with _services.ThreadPoolExecutor(max_workers=workers) as pool:
            for email, result in pool.map(_preflight, others):
                if isinstance(result, str):
                    return _services.jsonify({
                        "error": result + ". A campanha não foi iniciada.",
                    }), 400
                if result:
                    labels = [task.task_label or task.task_name or task.task_id
                              for task in tasks if task.task_id in result]
                    return _services.jsonify({
                        "error": (f"{email} não possui as categorias "
                                  f"selecionadas: {', '.join(labels)}. "
                                  "A campanha não foi iniciada.")
                    }), 400
                viable.append(by_email[email])
        accounts = viable
        if not accounts:
            return _services.jsonify({
                "error": "nenhuma conta passou no preflight. "
                         + "; ".join(skipped),
            }), 400

    from ...delivery.admission import campaign_create_blocker
    try:
        create_blocker = campaign_create_blocker(accounts)
    except (ValueError, OSError, RuntimeError):
        return _services.jsonify({"error_code": "create_receipts_unavailable",
                                 "error": "Não foi possível ler os recibos de CREATE. Os registros foram preservados; tente iniciar novamente após restabelecer a leitura local."}), 409
    if create_blocker:
        return _services.jsonify({"error_code": "app_check", "error": create_blocker}), 409

    # O MP4 é normalizado uma vez antes do lote; o usuário controla
    # quantos uploads compartilham o link de saída.
    account_workers = _services.campaign.clamp_account_workers(requested_workers, len(accounts))

    cfg = _services.CampaignConfig(accounts=accounts, tasks=tasks,
                         work_dir=_services.config.MEDIA_DATA_DIR / "ego4d",
                         timeout_blob=_services.campaign.DEFAULT_TIMEOUT_BLOB,
                         evaluate=True, finalize=True,
                         delay_mode=delay_mode, delay_s=delay_s,
                         account_gap_s=_services.campaign.DEFAULT_ACCOUNT_STAGGER_S,
                         account_workers=account_workers,
                         account_max_attempts=5,
                         account_retry_s=15,
                         require_all_accounts=False,
                         share_clips=True,
                         unique_video=False,
                         allow_new_accounts=False,
                         target_hours_per_account=target_hours,
                         run_until_exhausted=until_exhausted,
                         dataset_provider=dataset_provider,
                         content_mode=content_mode,
                         cleanup_after_upload=cleanup_after_upload,
                         realistic_timeline=True,
                         start_request_id=str(receipt_id) if receipt else None,
                         candidate_plan=receipt.get("candidate_plan") if receipt else None,
                         active_hours=active_hours)
    with _services._HEAVY_RUNNER_LOCK:
        if _services.campaign_drain["requested"]:
            return _services.campaign_closing_response()
        if receipt and receipt.get("sent_fingerprint") is not None:
            from ... import campaign_plan
            if receipt["sent_fingerprint"] != campaign_plan.registry_fingerprint():
                return _services.jsonify({"error_code": "preflight_history_changed", "error": "A lista de vídeos usados mudou. Revise a campanha novamente."}), 409
        if _services.RECOVERY.running:
            return _services.jsonify({"error": "Aguarde a operação de envio em andamento terminar."}), 409
        if _services.nymeria_library_worker.running:
            return _services.jsonify({"error": "Aguarde a preparação da Biblioteca terminar."}), 409
        try:
            unresolved = _services.recovery.snapshot()["items"]
        except (ValueError, OSError):
            return _services.jsonify({"error": "Não foi possível ler os registros de envios anteriores. A campanha não foi iniciada e os arquivos foram preservados."}), 409
        affected = sorted({item["email"] for item in unresolved if item.get("blocks_campaign", True)}
                          & {account.email for account in accounts})
        if affected:
            return _services.jsonify({"error": "Não foi possível identificar o conteúdo reservado por sessões anteriores destas contas: " + ", ".join(affected),
                            "error_code": "recovery_unidentified", "recovery_accounts": affected}), 409
        cfg.recovery_exclusions = _services.recovery.campaign_exclusions(unresolved)
        if _services.HOLO_CACHE_RUNNER.running:
            return _services.jsonify({
                "error": "pare o acelerador antes de iniciar a campanha",
            }), 409
        capacity_summary = None
        if target_hours > 0:
            # Re-read history/reservations even for a frozen review. This
            # admission happens before claiming a start or writing a new
            # campaign/journal; legacy callers cannot bypass it.
            from ... import campaign_plan
            try:
                with prepared_catalog_read() if content_mode == "dataset" else nullcontext():
                    candidate_plan, clip_review, _ = campaign_plan.build(cfg)
                capacity_summary = campaign_plan.capacity(
                    clip_review, [a.email for a in accounts], target_seconds=target_hours * 3600)
            except CatalogPreparationRequired as exc:
                return _services.jsonify({"code": "provider_catalog_preparation_required",
                                         "error": str(exc), "capacity": None}), 409
            except (ValueError, OSError, RuntimeError):
                return _services.jsonify({"error_code": "campaign_capacity_unavailable",
                                "error": "Não foi possível verificar o conteúdo novo para a meta. Revise a campanha novamente.",
                                "capacity": None}), 409
            if not capacity_summary["can_reach_goal"]:
                return _services.jsonify({"error_code": "campaign_capacity_insufficient",
                                "error": campaign_plan.capacity_error(capacity_summary),
                                "capacity": capacity_summary}), 409
            if cfg.candidate_plan is None:
                cfg.candidate_plan = candidate_plan
        # Readiness and recovery may take time. Validate the same reviewed
        # operation at admission, before its first local effect. Keep its
        # store entry protected until successful start consumes it.
        with _services.preflight_lock:
            if receipt:
                if _services.preflights.get(str(receipt_id)) is not receipt:
                    return _services.jsonify({"error_code": "preflight_missing", "error": "A prévia não está mais disponível. Revise a campanha novamente."}), 409
                if receipt["fingerprint"] != _services._preflight_fingerprint(receipt["fingerprint_emails"]):
                    return _services.jsonify({"error_code": "preflight_accounts_changed", "error": "O acesso ou a identidade de uma conta mudou. Revise a nova verificação."}), 409
                if receipt["expires"] <= _services.time.monotonic():
                    return _services.jsonify({"error_code": "preflight_expired", "error": "A prévia passou de 10 minutos. Atualize e revise antes de iniciar."}), 409
            if receipt and receipt["issues"]:
                if _services.BALANCES_RUNNER.running:
                    return _services.jsonify({"error": "Aguarde a consulta de saldos terminar."}), 409
                try:
                    _services._ban_accounts(receipt["issues"])
                except (OSError, ValueError) as exc:
                    return _services.jsonify({"error": "Não foi possível registrar e remover as contas. A campanha não iniciou."}), 500
            try:
                if receipt_id:
                    claimed,prior = _services.campaign_start_store.claim(str(receipt_id),kind="dataset",receipt_id=str(receipt_id),
                        body=receipt["body"], bindings={"accounts":[{"email":a.email,"org_key":a.org_key} for a in accounts],
                                                      "tasks":[t.task_id for t in tasks]})
                    if not claimed:
                        if prior["phase"] == "claimed":
                            return _services.jsonify({"error_code":"start_outcome_unknown","error":"O início exige consulta e revisão; não reenvie a operação."}),500
                        return _services.jsonify({**prior["reply"],"already_running":True})
                _services.RUNNER.start(cfg)
                if receipt_id:
                    _services.preflights.pop(str(receipt_id), None)
            except RuntimeError as exc:
                if receipt_id:
                    return _services.jsonify({"error_code":"start_outcome_unknown","error":"O início exige consulta e revisão; não reenvie a operação."}),500
                # Existing running-operation retries never start again.
                if _services.RUNNER.running:
                    return _services.jsonify({
                        "ok": True,
                        "already_running": True,
                        "state": _services.RUNNER.state,
                        "total_sends": _services.RUNNER.total_sends,
                    })
                return _services.jsonify({"error": str(exc)}), 409
            except (_services.campaign_start_store.StartStoreError,_services.OperationLeaseError,OSError):
                return _services.jsonify({"error_code":"start_state_unreadable","error":"Preserve o registro local de início para revisão."}),409
            except Exception:
                if receipt_id:
                    return _services.jsonify({"error_code":"start_outcome_unknown","error":"O início exige consulta e revisão; não reenvie a operação."}),500
                raise
    payload = {
        "ok": True,
        "total_sends": _services.RUNNER.total_sends,
        "selected_tasks": [
            {"task_id": t.task_id, "scenario": t.scenario} for t in tasks
        ],
        "accounts": [acc.email for acc in accounts],
        "removed_accounts": sorted({i["email"] for i in receipt["issues"]}) if receipt else [],
        "dataset": dataset_provider,
    }
    if skipped:
        payload["skipped_accounts"] = skipped
    if receipt and receipt["skipped_accounts"]:
        payload["skipped_accounts"] = receipt["skipped_accounts"]
    if receipt_id:
        payload["start_request_id"] = str(receipt_id)
        payload["preflight_id"] = str(receipt_id)
        try:
            _services.campaign_start_store.acknowledge(str(receipt_id),payload)
        except (ValueError,OSError):
            return _services.jsonify({"error_code":"start_outcome_unknown","error":"O início exige consulta e revisão; não reenvie a operação."}),500
    return _services.jsonify(payload)
