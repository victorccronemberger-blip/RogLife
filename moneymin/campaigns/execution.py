"""Main campaign execution and worker orchestration."""
from __future__ import annotations

import csv

def _run_campaign(_services, 
    config: CampaignConfig,
    log: CampaignLog | None = None,
    *,
    progress: Callable[[str, dict[str, Any]], bool | None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    prefetch: _ClipPrefetch,
) -> CampaignLog:
    """Executa a campanha: para cada task, escolhe clipes, prepara e envia para
    todas as contas. Devolve o log com os resultados por item.

    Hooks opcionais (usados pela interface web):
      - `progress(kind, payload)` — eventos de avanço: campaign_start,
        task_start, clip_prepare_start, clip_ready, account_start,
        account_done, item_done, campaign_done, campaign_stopped,
        storage_cleanup,
        delay_start/delay_tick (intervalo entre vídeos),
        window_wait_start/window_wait_tick (aguardando a janela de horário)
        e log (mensagens).
        authentication_wait é emitido após salvar e drenar o lote; retornar
        True autoriza revalidar o acesso e os recibos após atenção externa.
        Sem esse reconhecimento explícito, a execução termina preservada.
      - `should_stop() -> bool` — se True, a campanha para cooperativamente
        entre clipes/contas e durante as esperas; o log parcial é salvo mesmo assim.
    O log é salvo de forma INCREMENTAL após cada item (um crash não perde a
    campanha inteira).
    - Dedup: a seleção automática pula clipes já enviados a TODAS as contas
      (`data/sent_videos.json`, ver `sent_registry`); no envio, contas que já
      receberam o clipe são puladas (`account_done` com `skipped=True`). Se não
      sobrar nenhum clipe novo de um cenário (100% enviado), informa esgotamento;
      limpar o histórico exige um reset explícito.
    - `config.delay_mode`: aplicado na TROCA de vídeo (nunca entre contas do
      mesmo vídeo): "clip" = espera a duração do vídeo recém-enviado
      (parece gravação real), "fixed" = `delay_s` segundos, "off" = sem espera.
    - `config.account_workers`: contas em voo por vídeo, cortado por
      `max_account_workers()` (teto padrão 6 depois de um único ffmpeg).
      `account_gap_s` 0 = sem espera entre contas.
      A espera de gravação corre em paralelo para todas as contas do lote,
      sem ocupar workers; cada envio só é liberado ao fim da sua reserva.
    - `config.active_hours`: janela local de envio (ex.: (7, 18)) — fora dela a
      campanha aguarda a próxima abertura antes de cada envio.
    - `config.cleanup_after_upload`: baixa e prepara somente o item em uso,
      envia para suas contas e libera os arquivos gerenciados após confirmação.
      Um envio pendente impede a aquisição de outro item. Prefetch de mídia e
      retenção de cache ficam disponíveis somente quando a limpeza é desligada.
    """
    sends = {"ok": 0, "failed": 0, "skipped": 0}

    def _emit(kind: str, **payload: Any) -> bool | None:
        if kind == "account_done":
            key = "skipped" if payload.get("skipped") else (
                "ok" if payload.get("ok") else "failed")
            sends[key] += 1
        elif kind == "campaign_stopped":
            with log._save_lock:
                log.status = "stopped"
        elif (kind in ("task_error", "task_empty", "task_exhausted", "task_shortfall", "goal_shortfall",
                       "authentication_wait")
              or (kind == "clip_prepare_done" and not payload.get("ok"))):
            with log._save_lock:
                log.issues.append({"kind": kind, **payload})
                log.save()
        if progress:
            return progress(kind, payload)

    def _log(msg: str) -> None:
        try:
            print(msg)
        except (OSError, UnicodeError):
            # stdout quebrado (ex.: servidor web órfão, sem console/pipe vivo) —
            # ou console com encoding limitado. O print nunca pode derrubar a
            # campanha; o evento basta para a UI.
            pass
        _emit("log", message=msg)

    def _cleanup_on_stop(
        item: dict[str, Any] | None,
        clip: dict[str, Any] | None,
        *,
        cleanup_candidate: bool = True,
    ) -> None:
        if not config.cleanup_after_upload:
            return
        try:
            results = _services._cleanup_stopped_prepared_media(
                item, clip, work_dir, prefetch, cleanup_candidate=cleanup_candidate)
        except Exception as exc:
            with log._save_lock:
                log.issues.append({"kind": "storage_cleanup_error",
                                   "clip_uid": (clip or {}).get("clip_uid"),
                                   "error_type": type(exc).__name__})
                log.save()
            _emit("storage_cleanup", clip_uid=(clip or {}).get("clip_uid"),
                  files=0, bytes=0, errors=[type(exc).__name__],
                  protected=0, retained_managed=1, retained_bytes=0)
            _log(f"  armazenamento: mídia preservada após parada ({type(exc).__name__})")
            return
        for result in results:
            _emit("storage_cleanup", clip_uid=(clip or {}).get("clip_uid"),
                  files=result["files"], bytes=result["bytes"],
                  errors=result["errors"], protected=result["protected"],
                  retained_managed=result.get("retained_managed", 0),
                  retained_bytes=result.get("retained_bytes", 0))
            if result["errors"]:
                with log._save_lock:
                    log.issues.append({"kind": "storage_cleanup_error",
                                       "clip_uid": (clip or {}).get("clip_uid"),
                                       "errors": list(result["errors"])})
                    log.save()
                _log("  armazenamento: houve erro ao limpar mídia após a parada; detalhes no histórico")
            elif result.get("retained_managed", 0):
                with log._save_lock:
                    log.issues.append({"kind": "storage_retained_after_stop",
                                       "clip_uid": (clip or {}).get("clip_uid"),
                                       "retained_managed": result.get("retained_managed", 0),
                                       "retained_bytes": result.get("retained_bytes", 0),
                                       "protected": result.get("protected", 0)})
                    log.save()
                _log("  armazenamento: mídia gerenciada preservada por proteção local; detalhes no evento de limpeza")

    def _maybe_shuffle(items: list) -> list:
        out = list(items)
        if config.shuffle_schedule and len(out) > 1:
            _services.random.shuffle(out)
        return out

    dataset_provider = _services.normalize_dataset_provider(config.dataset_provider)
    content_mode = _services.normalize_content_mode(config.content_mode)

    log = log or _services.CampaignLog(started_at=_services._recorded_at_now(),
                             accounts=[a.email for a in config.accounts])
    work_dir = _services.Path(config.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    sessions: dict[str, Session] = {}
    banned: set[str] = set()
    rejected_imu: set[str] = set()
    imu_rejected: dict[str, dict[str, Any]] = {}
    account_seconds: dict[str, float] = {}
    batch_footage: dict[str, list[dict[str, Any]]] = {}

    def _batch_reserved_emails(candidate: dict) -> set[str]:
        return {email for email, previous in batch_footage.items()
                if any(_services._batch_footage_overlaps(clip, candidate) for clip in previous)}

    def _reserve_batch_footage(email: str, candidate: dict) -> dict:
        reservation = {key: candidate[key] for key in (
            "clip_uid", "source", "parent_video_uid", "window_s", "dedup_clip_uids",
            "device_window_ns", "planned_device_window_ns", "source_clock_domain",
            "acquisition_required")
            if key in candidate}
        batch_footage.setdefault(email, []).append(reservation)
        return reservation

    def _note_retained_media(cleanup: dict[str, Any], clip_info: dict[str, Any], tsk) -> None:
        """Registra mídia gerenciada preservada por proteção local, sem parar.

        Os envios confirmados já estão liberados; arquivos ainda reservados por
        registros anteriores (recibos pendentes, falha não reconhecida) ou pelo
        preparo do próximo trecho permanecem no lugar. A pendência fica visível
        no Histórico para revisão, e a campanha continua com o próximo vídeo —
        o reuso do mesmo conteúdo não baixa bytes novos nem interrompe o fluxo.
        """
        retained = {
            "kind": "storage_retained",
            "clip_uid": clip_info["clip_uid"],
            "retained_managed": cleanup.get("retained_managed", 0),
            "retained_bytes": cleanup.get("retained_bytes", 0),
            "retained_names": cleanup.get("retained_names", []),
            "protected": cleanup.get("protected", 0),
        }
        with log._save_lock:
            log.issues.append(retained)
            log.save()
        _log(
            "  armazenamento: "
            f"{retained['retained_managed']} arquivo(s) preservado(s) por proteção local "
            f"({retained['retained_bytes'] / (1024 ** 2):.1f} MB); registrado no histórico; "
            "o próximo vídeo segue")
        _emit("item_incomplete", clip_uid=clip_info["clip_uid"],
              task=tsk.scenario, reason="media_retained_for_recovery",
              retained_managed=retained["retained_managed"],
              retained_bytes=retained["retained_bytes"])

    def _record_imu_gap(clip_info: dict[str, Any], tsk, display_name: str, exc: Exception) -> None:
        """Persiste a lacuna real de IMU para novas campanhas não repetirem.

        A medição é independente do retorno do preparo: só grava quando o CSV
        local prova menos que o mínimo pedido. Falha de medição ou gravação não
        mata a campanha — fica como pendência visível no Histórico.
        """
        try:
            longest = _services.imu_rejections.longest_continuous_s(
                clip_info, work_dir, max_dur_s=float(tsk.max_dur_s))
            if longest is None or longest >= float(tsk.min_dur_s):
                return
            _services.imu_rejections.record(
                clip_info, min_dur_s=float(tsk.min_dur_s), longest_continuous_s=longest,
                detail=f"{type(exc).__name__}: {exc}", task_id=tsk.task_id,
                task_name=display_name)
        except Exception as exc2:  # noqa: BLE001 — pendência visível, campanha segue
            _log(f"  [!] lacuna de IMU não registrada p/ reuso ({type(exc2).__name__}: {exc2})")
            with log._save_lock:
                log.issues.append({"kind": "imu_rejection_error",
                                   "clip_uid": clip_info.get("clip_uid"),
                                   "error": f"{type(exc2).__name__}: {exc2}"})
                log.save()

    until_exhausted = config.run_until_exhausted
    quota_s = 0.0 if until_exhausted else max(0.0, float(config.target_hours_per_account or 0) * 3600.0)
    n_tasks = max(1, len(config.tasks))
    per_task_cap_s = (quota_s / n_tasks) * 1.3 if quota_s else 0.0
    _emit("campaign_start", accounts=[a.email for a in config.accounts],
          tasks=[t.task_name or t.scenario for t in config.tasks],
          dataset=dataset_provider, content_mode=content_mode,
          cleanup_after_upload=bool(config.cleanup_after_upload))

    # Fica True somente depois que um vídeo foi realmente enviado. O intervalo
    # é consumido uma vez antes do próximo candidato útil; falhas de prepare e
    # clipes pulados não podem criar uma segunda espera.
    delay_pending = False
    last_dur_s = 0.0  # duração do último clipe enviado (p/ delay_mode="clip")
    last_batch_started_at: float | None = None
    ordered_tasks = _maybe_shuffle(list(config.tasks))
    # First distribute across categories, then use the remaining reviewed content
    # without a per-category ceiling when another category cannot fill its share.
    schedule = [(task, per_task_cap_s) for task in ordered_tasks]
    if quota_s and len(ordered_tasks) > 1:
        schedule += [(task, quota_s) for task in ordered_tasks if not task.clip_uids]
    for tsk, per_task_cap_s in schedule:
        if quota_s and all(a.email in banned or account_seconds.get(a.email, 0) >= quota_s
                           for a in config.accounts):
            break
        if should_stop and should_stop():
            _log("  [!] campanha interrompida pelo usuário")
            _emit("campaign_stopped", reason="parada pelo usuário")
            _cleanup_on_stop(None, None, cleanup_candidate=False)
            break
        display_name = tsk.task_label or tsk.task_name or tsk.scenario
        registry_key = tsk.registry_key
        selection_limit = "até esgotar o conteúdo" if until_exhausted else f"n={tsk.count}"
        _log(f"\n=== categoria={display_name} ({selection_limit}) ===")
        _emit("task_start", scenario=tsk.scenario, task_name=display_name,
              count=tsk.count, run_until_exhausted=until_exhausted)
        automatic_selection = not tsk.clip_uids
        try:
            imu_rejected = _services.imu_rejections.load()
            if tsk.clip_uids:
                # Seleção explícita continua sujeita às mesmas regras da
                # automática; config antiga/manual não pode furar o matching.
                clips = []
                ego_compatible: dict[str, dict[str, Any]] | None = None
                if (dataset_provider in ("all", "ambos", "ego4d") and tsk.task_name
                        and _services.task_matching.rule_for(tsk.task_name)):
                    compatible_ego = list(_services._compatible_task_clips(
                        tsk.task_name, "ego4d", min_dur_s=tsk.min_dur_s,
                        max_dur_s=tsk.max_dur_s))
                    ego_compatible = {
                        candidate["clip_uid"]: candidate
                        for candidate in compatible_ego
                        if tsk.min_dur_s <= float(candidate.get("dur_s") or 0)
                        <= tsk.max_dur_s
                    }
                for uid in tsk.clip_uids:
                    if uid.startswith(("nymeria:", "nymeria-planned:")):
                        if dataset_provider not in {"all", "ambos", "nymeria"}:
                            _log(f"  [!] clipe {uid} pertence ao Nymeria — pulando")
                            continue
                        compatible = {
                            candidate["clip_uid"]: candidate
                            for candidate in _services.nymeria.automatic_candidates(
                                task_name=tsk.task_name, task_id=tsk.task_id,
                                registry_key=tsk.registry_key,
                                min_dur_s=tsk.min_dur_s,
                                max_dur_s=tsk.max_dur_s,
                                include_planned=content_mode != "cache",
                            )
                        }
                        nymeria_clip = compatible.get(uid)
                        if nymeria_clip is None:
                            _log(
                                f"  [!] clipe {uid} incompatível com "
                                f"'{display_name}' — pulando"
                            )
                            continue
                        clips.append(nymeria_clip)
                        continue
                    if uid.startswith("holoassist:"):
                        if dataset_provider in {"ego4d", "nymeria", "ambos"}:
                            _log(f"  [!] clipe {uid} pertence ao HoloAssist — pulando")
                            continue
                        # O UID explícito não pode furar categoria, duração ou
                        # política de qualidade. Só aceita se também estiver no
                        # pool estrito da tarefa selecionada.
                        compatible = {
                            candidate["clip_uid"]: candidate
                            for candidate in _services.holoassist.list_clips(
                                tsk.task_name or "",
                                min_dur_s=tsk.min_dur_s,
                                max_dur_s=tsk.max_dur_s,
                            )
                        }
                        holo_clip = compatible.get(uid)
                        if holo_clip is None:
                            _log(
                                f"  [!] clipe {uid} incompatível com "
                                f"'{display_name}' — pulando"
                            )
                            continue
                        clips.append(holo_clip)
                        continue
                    if dataset_provider == "holoassist":
                        _log(f"  [!] clipe {uid} não pertence ao HoloAssist — pulando")
                        continue
                    if dataset_provider == "nymeria":
                        _log(f"  [!] clipe {uid} não pertence ao Nymeria — pulando")
                        continue
                    if ego_compatible is not None:
                        candidate = ego_compatible.get(uid)
                        if candidate is None:
                            _log(
                                f"  [!] clipe {uid} incompatível com "
                                f"'{display_name}' — pulando"
                            )
                            continue
                        clips.append(candidate)
                        continue
                    clip, video = _services.ego4d.find_clip(uid)
                    if clip is None or video is None:
                        _log(f"  [!] clipe {uid} não encontrado no manifest — pulando")
                        continue
                    candidate = _services.ego4d._clip_record(clip, video)
                    if not (tsk.min_dur_s <= float(candidate.get("dur_s") or 0)
                            <= tsk.max_dur_s):
                        _log(f"  [!] clipe {uid} fora da duração permitida — pulando")
                        continue
                    clips.append(candidate)
            else:
                # seleção automática: pula clipes já enviados a TODAS as contas
                # (registro em data/sent_videos.json — ver sent_registry)
                emails = [a.email for a in config.accounts]
                shorts = _services.automatic_candidates(tsk, config)
                all_clips = shorts
                fresh = []
                new_recipient_counts = {}
                used_parents = set()
                rejected_n = 0
                for candidate in all_clips:
                    if (candidate["clip_uid"] in rejected_imu
                            or _services.imu_rejections.blocking_entry(
                                imu_rejected, candidate, tsk.min_dur_s)):
                        rejected_n += 1
                        continue
                    reserved_for = (_services._candidate_reserved_emails(config, candidate)
                                    | _batch_reserved_emails(candidate))
                    unavailable = _services._candidate_sent_emails(registry_key, candidate) | reserved_for
                    if _services.sent_registry.is_sent_to_all(registry_key, candidate["clip_uid"], emails) or all(email in unavailable for email in emails):
                        used_parents.add(_services.parent_key(candidate))
                    else:
                        fresh.append(candidate)
                        new_recipient_counts[candidate["clip_uid"]] = sum(email not in unavailable for email in emails)
                skipped_n = len(all_clips) - len(fresh)
                if skipped_n:
                    _log(f"  (pulando {skipped_n} clipe(s) já enviados, reservados ou reprovados nos sensores)")
                if not fresh and all_clips:
                    _log(f"  [i] catálogo elegível esgotado para '{display_name}'; histórico preservado")
                    _emit("task_exhausted", scenario=tsk.scenario, task_name=display_name,
                          eligible=len(all_clips), rejected_imu=rejected_n)
                    continue
                hours = sum(float(c.get("dur_s") or 0) for c in fresh) / 3600
                merged_n = sum(1 for c in fresh if c.get("needs_cut"))
                _log(f"  pool: {len(shorts)} trechos puros → {len(fresh)} "
                     f"sessões ({merged_n} cortes do vídeo-pai, {hours:.1f}h)")
                clips = _services._prefer_cached_clips(_services.diverse_order(_services.ego4d.prefer_long_clips(
                    fresh, shuffle=config.shuffle_schedule,
                    prefer_parent_cuts=True), used_parents=used_parents),
                    work_dir, prioritize=content_mode != "dataset")
                # Prefer content that serves more accounts, keeping cache/diversity
                # ordering within each group. Partially sent clips remain fallback.
                clips.sort(key=lambda clip: -new_recipient_counts[clip["clip_uid"]])
                _emit("content_pool", task_name=display_name, clips=len(clips),
                      **_services.diversity_summary(clips))
        except Exception as exc:  # noqa: BLE001 — uma categoria não mata as demais
            error = f"{type(exc).__name__}: {exc}"
            _log(f"  [!] falha ao selecionar clipes: {error}")
            _emit("task_error", scenario=tsk.scenario, task_name=display_name,
                  error=error)
            continue
        if tsk.clip_uids:
            clips = _maybe_shuffle(clips)
            if content_mode == "cache":
                clips = [clip for clip in clips if _services._clip_is_cached(clip, work_dir)]
            if config.cleanup_after_upload:
                clips = [
                    {**clip, "_cache_ready_at_selection": True}
                    if _services._clip_is_cached(clip, work_dir) else clip
                    for clip in clips
                ]
        if not clips:
            _log(f"  [!] nenhum clipe encontrado p/ cenário '{tsk.scenario}'")
            _emit("task_empty", scenario=tsk.scenario, task_name=display_name,
                  min_dur_s=tsk.min_dur_s, max_dur_s=tsk.max_dur_s,
                  dataset=dataset_provider)
            continue
        task_accounts = _maybe_shuffle(list(config.accounts))
        task_sends: dict[str, int] = {}
        task_seconds: dict[str, float] = {}
        for clip_info in clips:
            if (clip_info["clip_uid"] in rejected_imu
                    or _services.imu_rejections.blocking_entry(
                        imu_rejected, clip_info, tsk.min_dur_s)):
                continue
            if len(banned) == len(config.accounts):
                break
            if quota_s and all(
                    a.email in banned or account_seconds.get(a.email, 0) >= quota_s
                    or task_seconds.get(a.email, 0) >= per_task_cap_s
                    for a in config.accounts):
                break
            if automatic_selection and not quota_s and not until_exhausted and all(
                    a.email in banned or task_sends.get(a.email, 0) >= tsk.count
                    for a in config.accounts):
                break
            if should_stop and should_stop():
                _log("  [!] campanha interrompida pelo usuário")
                _emit("campaign_stopped", reason="parada pelo usuário")
                _cleanup_on_stop(None, clip_info, cleanup_candidate=False)
                break
            # anti-desperdício: se TODAS as contas da campanha já receberam este
            # clipe (seleção explícita do wizard ou corrida entre campanhas),
            # pula ANTES do intervalo e de baixar/reencodar.
            sent_to = _services._candidate_sent_emails(registry_key, clip_info)
            batch_reserved = _batch_reserved_emails(clip_info)
            reserved = _services._candidate_reserved_emails(config, clip_info) | batch_reserved
            eligible_accounts = [a for a in config.accounts if a.email not in banned
                                 and (not quota_s or (account_seconds.get(a.email, 0) < quota_s
                                      and task_seconds.get(a.email, 0) < per_task_cap_s))
                                 and (until_exhausted or quota_s or not automatic_selection
                                      or task_sends.get(a.email, 0) < tsk.count)]
            if not eligible_accounts or all(a.email in sent_to | reserved for a in eligible_accounts):
                _log(f"  clipe {clip_info['clip_uid'][:12]} já enviado ou reservado por pendência "
                     f"em todas as contas — pulando (sem baixar/reencodar)")
                # account_done mantém o progresso da UI consistente; o item nem
                # chega a existir (nada a registrar no log da campanha)
                for account in eligible_accounts:
                    _emit("account_done", clip_uid=clip_info["clip_uid"],
                          task=tsk.scenario, email=account.email, ok=account.email not in reserved,
                          skipped=True, reason=("source_reserved_in_campaign" if account.email in batch_reserved
                                               else "pending_recovery" if account.email in reserved else "already_sent"))
                continue
            if content_mode == "cache" and not _services._clip_is_cached(clip_info, work_dir):
                _emit("clip_prepare_done", clip_uid=clip_info["clip_uid"],
                      task_name=display_name, ok=False,
                      error="clipe saiu do cache antes do preparo")
                continue
            # Intervalo ENTRE VÍDEOS: é devido uma única vez depois de um envio
            # bem-sucedido. Assim, um prepare que falhar após esta espera não faz
            # o próximo clipe aguardar tudo novamente.
            if delay_pending:
                gap = 0.0
                if config.delay_mode == "clip":
                    elapsed = (_services.time.monotonic() - last_batch_started_at
                               if last_batch_started_at is not None else 0.0)
                    # Ritmo entre INÍCIOS de vídeos. O tempo consumido em
                    # encode/upload já conta; não espere a duração inteira duas
                    # vezes quando o lote levou tanto quanto o próprio vídeo.
                    gap = max(0.0, last_dur_s - elapsed)
                elif config.delay_mode == "fixed":
                    gap = config.delay_s
                if config.shuffle_schedule and config.delay_mode != "off":
                    # Jitter no intervalo: não corta vídeo, só tira o metrônomo.
                    gap += _services.random.uniform(20.0, 90.0)
                if gap > 0:
                    _emit("delay_start", delay_s=gap, mode=config.delay_mode)
                    if _services._interruptible_sleep(gap, should_stop, _emit,
                                            tick_every=_services._tick_every(gap)):
                        _log("  [!] campanha interrompida durante o intervalo")
                        _emit("campaign_stopped",
                              reason="parada durante o intervalo")
                        _cleanup_on_stop(None, clip_info, cleanup_candidate=False)
                        break
                delay_pending = False
            _log(f"  clipe: {clip_info['clip_uid'][:12]} "
                 f"{clip_info.get('dur_s')}s "
                 f"device={clip_info.get('device')}")
            _emit("clip_prepare_start", clip_uid=clip_info["clip_uid"],
                  task=tsk.scenario, dur_s=clip_info["dur_s"])
            item: dict[str, Any] | None = None
            try:
                item = prefetch.take(clip_info["clip_uid"])
                if item is None:
                    def _prepare_progress(
                        phase: str,
                        payload: dict[str, Any],
                        _clip_uid: str = str(clip_info["clip_uid"]),
                    ) -> None:
                        if should_stop and should_stop():
                            raise RuntimeError("preparo interrompido pelo usuário")
                        _emit(
                            "clip_prepare_progress",
                            clip_uid=_clip_uid,
                            phase=phase,
                            **payload,
                        )

                if item is None and clip_info.get("source") == "holoassist":
                    item = _services.prepare_holoassist_clip(
                        clip_info, work_dir, progress=_prepare_progress,
                        allow_download=content_mode != "cache",
                    )
                if item is None and clip_info.get("source") == "nymeria":
                    item = _services.prepare_nymeria_clip(
                        clip_info, work_dir, progress=_prepare_progress,
                        allow_download=content_mode != "cache",
                        should_stop=should_stop,
                    )
                if item is None:
                    clip, video = _services._ego_clip_inputs(clip_info)
                    if clip is None or video is None:
                        raise RuntimeError("clipe ou vídeo pai ausente no manifest")
                    item = _services.prepare_clip(
                        clip, video, work_dir, progress=_prepare_progress,
                        allow_download=content_mode != "cache")
                # A duração real pode diferir das anotações ou de um cache antigo.
                # Nunca envie um MP4 que ultrapasse o teto escolhido.
                prepared_duration = float(item["duration_ms"])
                if not _services.math.isfinite(prepared_duration) or prepared_duration <= 0:
                    raise ValueError("vídeo preparado sem duração válida")
                # Encoding/cache validation already allows up to one second
                # of container rounding; reject genuinely shortened footage.
                if prepared_duration + 1000 < tsk.min_dur_s * 1000:
                    raise ValueError(
                        f"vídeo preparado abaixo da duração mínima de {tsk.min_dur_s:g}s"
                    )
                if prepared_duration > tsk.max_dur_s * 1000:
                    raise ValueError(
                        f"vídeo preparado excede a duração máxima de {tsk.max_dur_s:g}s"
                    )
            except Exception as exc:  # noqa: BLE001 — pula o clipe, segue a campanha
                if should_stop and should_stop():
                    _emit("campaign_stopped", reason="parada durante aquisição ou preparo")
                    _cleanup_on_stop(item, clip_info)
                    break
                # Erros de parse/decodificação do CSV de IMU (ex.: linha maior
                # que o limite do csv em arquivo corrompido) são falha de IMU,
                # não falha de aquisição: o clipe é descartado e a campanha segue.
                imu_fail = isinstance(exc, (csv.Error, UnicodeDecodeError)) or any(
                    reason in str(exc).lower() for reason in (
                        "cobertura imu insuficiente", "sem amostras válidas de imu",
                        "sem cobertura contínua de imu", "atravessa trecho sem cobertura",
                        "sem imu real"))
                carved_item = None
                if imu_fail and not clip_info.get("imu_carved_from"):
                    def _carve_progress(
                        phase: str,
                        payload: dict[str, Any],
                        _clip_uid: str = str(clip_info["clip_uid"]),
                    ) -> None:
                        _emit("clip_prepare_progress", clip_uid=_clip_uid,
                              phase=phase, **payload)

                    carved_item = _services._try_prepare_imu_carve(
                        clip_info, work_dir,
                        min_dur_s=float(tsk.min_dur_s),
                        max_dur_s=float(tsk.max_dur_s),
                        allow_download=content_mode != "cache",
                        progress=_carve_progress,
                        log=_log,
                        emit=_emit,
                        display_name=display_name,
                    )
                if carved_item is None:
                    if imu_fail:
                        rejected_imu.add(clip_info["clip_uid"])
                        _record_imu_gap(clip_info, tsk, display_name, exc)
                    error = f"{type(exc).__name__}: {exc}"
                    _log(f"    [!] prepare falhou: {error}")
                    _emit("clip_prepare_done", clip_uid=clip_info["clip_uid"],
                          task_name=display_name, ok=False, error=error)
                    if config.cleanup_after_upload:
                        cleanup = _services._cleanup_rejected_prepare(clip_info, work_dir)
                        _emit("storage_cleanup", clip_uid=clip_info["clip_uid"],
                              files=cleanup["files"], bytes=cleanup["bytes"],
                              errors=cleanup["errors"], protected=cleanup["protected"])
                        # A rejected sensor window is safe to skip after its
                        # owned, unused files are released. Acquisition/encode
                        # failures may hold partial downloads: stop this cycle.
                        if (not imu_fail or cleanup["errors"]
                                or cleanup.get("retained_managed", 0)):
                            raise RuntimeError(
                                "Preparo interrompido; nenhum outro vídeo foi adquirido. " + error) from exc
                    continue
                item = carved_item["item"]
                clip_info = carved_item["clip"]
                prepared_duration = float(item["duration_ms"])
                if (not _services.math.isfinite(prepared_duration) or prepared_duration <= 0
                        or prepared_duration + 1000 < tsk.min_dur_s * 1000
                        or prepared_duration > tsk.max_dur_s * 1000):
                    rejected_imu.add(clip_info["clip_uid"])
                    _log("    [!] carve fora da janela de duração")
                    _emit("clip_prepare_done", clip_uid=clip_info["clip_uid"],
                          task_name=display_name, ok=False,
                          error="carve fora da duração")
                    if config.cleanup_after_upload:
                        _services._cleanup_uploaded_item(item, work_dir)
                        raise RuntimeError(
                            "Recorte preparado fora da duração; nenhum outro vídeo foi adquirido.")
                    continue
            # Acquisition resolves catalog-only Nymeria identities against the
            # measured SDK clock before dedup, reservations or delivery.
            if clip_info.get("acquisition_required") is True:
                resolved = item.get("_content_candidate")
                if not isinstance(resolved, dict) or resolved.get("acquisition_required") is True:
                    raise ValueError("Fonte Nymeria adquirida sem candidato medido confirmado.")
                clip_info = dict(resolved)
            # IMU carving can produce a different proven source interval.
            # Account eligibility follows the footage that will actually go out.
            batch_reserved = _batch_reserved_emails(clip_info)
            reserved = _services._candidate_reserved_emails(config, clip_info) | batch_reserved
            sent_to = _services._candidate_sent_emails(registry_key, clip_info)
            _emit("clip_ready", clip_uid=clip_info["clip_uid"],
                  duration_ms=item["duration_ms"], imu_real=item["imu_real"],
                  source_provenance=item.get("source_provenance"))
            # Só antecipa outro clipe quando ele será realmente necessário.
            # Uma campanha n=1 não deve baixar material residual em fundo.
            current_dur_s = float(item.get("duration_ms") or 0) / 1000.0
            if quota_s:
                # Adiante o próximo quando ele ainda será necessário, mas não
                # baixe um clipe órfão se este já completa a meta/cota da task.
                should_prefetch = any(
                    account_seconds.get(account.email, 0.0) + current_dur_s
                    < quota_s
                    and task_seconds.get(account.email, 0.0) + current_dur_s
                    < per_task_cap_s
                    for account in config.accounts
                )
            elif until_exhausted:
                should_prefetch = any(a.email not in banned for a in task_accounts)
            else:
                available_accounts = [a for a in task_accounts
                                      if a.email not in banned and a.email not in sent_to | reserved
                                      and task_sends.get(a.email, 0) < tsk.count]
                current_recipients = {a.email for a in (
                    available_accounts if config.share_clips else available_accounts[:1])}
                should_prefetch = any(
                    a.email not in banned
                    and task_sends.get(a.email, 0) + int(a.email in current_recipients) < tsk.count
                    for a in task_accounts)
            if (should_prefetch and content_mode != "cache"
                    and not config.cleanup_after_upload):
                _services._prefetch_following(
                    prefetch,
                    clips,
                    clip_info["clip_uid"],
                    [account.email for account in config.accounts],
                    registry_key,
                )
            item["clip_uid"] = clip_info["clip_uid"]
            item["task_id"] = tsk.task_id
            item["requires_audio"] = tsk.requires_audio
            item["task_name"] = display_name
            item["task_name_authoritative"] = tsk.task_name
            item["task_scenario"] = tsk.scenario
            item["registry_key"] = registry_key
            item["dedup_clip_uids"] = list(clip_info.get("dedup_clip_uids") or [])
            item['_history_name'] = log._path.name if log._path is not None else None
            item["accounts"] = []
            pending_accounts: list[AccountSpec] = []
            account_results: dict[str, dict[str, Any]] = {}
            persisted_item: dict[str, Any] | None = None

            def _persist_item_results() -> None:
                nonlocal persisted_item
                with log._save_lock:
                    item["accounts"] = [account_results[a.email] for a in config.accounts
                                        if a.email in account_results]
                    if persisted_item is None:
                        persisted_item = {k: v for k, v in item.items()
                                          if k not in ("imu_csv", "frames_csv", "probe", "_cleanup_paths", "_history_name")}
                        log.add_item(persisted_item)
                    else:
                        persisted_item["accounts"] = item["accounts"]
                    log.save()
            for account in task_accounts:
                if should_stop and should_stop():
                    _log("  [!] campanha interrompida pelo usuário")
                    _emit("campaign_stopped", reason="parada pelo usuário")
                    break
                if account.email in banned:
                    skip_res = {"email": account.email, "org_key": account.org_key,
                                "ok": False, "skipped": True,
                                "error": "conta desativada — fora desta campanha"}
                    account_results[account.email] = skip_res
                    _emit("account_done", clip_uid=clip_info["clip_uid"],
                          task=tsk.scenario, email=account.email, ok=False,
                          skipped=True, error=skip_res["error"])
                    continue
                if quota_s:
                    if account_seconds.get(account.email, 0) >= quota_s:
                        continue
                    if task_seconds.get(account.email, 0) >= per_task_cap_s:
                        continue
                elif automatic_selection and not until_exhausted and task_sends.get(account.email, 0) >= tsk.count:
                    continue
                if not config.allow_new_accounts:
                    age = _services.device_profile.profile_age_days(account.email)
                    if age < float(config.min_account_age_days or 0):
                        skip_res = {
                            "email": account.email, "org_key": account.org_key,
                            "ok": True, "skipped": True,
                            "error": (f"nova demais: {age:.1f}d de aparelho "
                                      f"(mínimo {config.min_account_age_days:g}d)"),
                        }
                        account_results[account.email] = skip_res
                        _log(f"      -> {account.email} (nova demais — pulando)")
                        _emit("account_done", clip_uid=clip_info["clip_uid"],
                              task=tsk.scenario, email=account.email, ok=True,
                              skipped=True, error=skip_res["error"])
                        continue
                if account.email in batch_reserved:
                    account_results[account.email] = {"email": account.email, "org_key": account.org_key,
                        "ok": False, "skipped": True, "reason": "source_reserved_in_campaign"}
                    _emit("account_done", clip_uid=clip_info["clip_uid"], task=tsk.scenario,
                          email=account.email, ok=False, skipped=True, reason="source_reserved_in_campaign")
                    continue
                if account.email in reserved:
                    _log(f"      -> {account.email} (clipe reservado por envio anterior pendente)")
                    account_results[account.email] = {"email": account.email, "org_key": account.org_key,
                        "ok": False, "skipped": True, "reason": "pending_recovery"}
                    _emit("account_done", clip_uid=clip_info["clip_uid"], task=tsk.scenario,
                          email=account.email, ok=False, skipped=True, reason="pending_recovery")
                    continue
                # dedup por conta: quem já recebeu este clipe é pulado
                if account.email in _services._candidate_sent_emails(registry_key, clip_info):
                    _log(f"      -> {account.email} (já recebeu este clipe — pulando)")
                    skip_res = {"email": account.email, "org_key": account.org_key,
                                "ok": True, "skipped": True, "reason": "already_sent"}
                    account_results[account.email] = skip_res
                    # account_done mantém o progresso da UI consistente
                    _emit("account_done", clip_uid=clip_info["clip_uid"],
                          task=tsk.scenario, email=account.email, ok=True,
                          skipped=True, reason="already_sent")
                    continue
                pending_accounts.append(account)

            if should_stop and should_stop():
                _log("  [!] campanha interrompida pelo usuário")
                _emit("campaign_stopped", reason="parada pelo usuário")
                _cleanup_on_stop(item, clip_info)
                break

            if pending_accounts and not config.share_clips:
                pending_accounts = pending_accounts[:1]
                _log(f"  clipe exclusivo para {pending_accounts[0].email} "
                     "(não replica o mesmo vídeo nas outras contas)")

            workers = _services.clamp_account_workers(
                int(config.account_workers or 1),
                len(pending_accounts) or 1)
            warm_pool = None
            if config.unique_video and not config.cleanup_after_upload:
                # Re-encode só das que vão sair agora (+1 de folga).
                warm_n = min(len(pending_accounts), workers + 1)
                warm_pool = _services._warm_account_videos(
                    _services.Path(item["video_path"]),
                    [account.email for account in pending_accounts[:warm_n]])
            warm_state: list[_services.ThreadPoolExecutor | None] = [warm_pool]

            def _stop_warm(
                state: list[ThreadPoolExecutor | None] = warm_state,
            ) -> None:
                pool = state[0]
                state[0] = None
                if pool is not None:
                    pool.shutdown(wait=False, cancel_futures=True)

            # A janela vale para o lote: todas as contas deste vídeo começam
            # dentro do horário permitido.
            if pending_accounts and config.active_hours:
                if _services._wait_for_window(config.active_hours, should_stop, _emit):
                    _log("  [!] campanha interrompida fora do horário de envio")
                    _emit("campaign_stopped", reason="parada fora do horário")
                    _stop_warm()
                    _cleanup_on_stop(item, clip_info)
                    break

            def _send_account(
                upload_idx: int,
                account: AccountSpec,
                scheduled_recorded_at: str | None = None,
                retry_of_session_id: str | None = None,
                clip_uid: str = clip_info["clip_uid"],
                task_scenario: str = tsk.scenario,
                task_id: str = tsk.task_id,
                upload_item: dict[str, Any] = item,
            ) -> dict[str, Any]:
                _log(f"      -> {account.email}")
                _emit("account_start", clip_uid=clip_uid,
                      task=task_scenario, email=account.email)

                def _account_progress(phase: str, state: str,
                                      attempt: int, **details: Any) -> None:
                    _emit("account_progress", clip_uid=clip_uid,
                          task=task_scenario, email=account.email,
                          phase=phase, state=state, attempt=attempt,
                          **details)  # noqa: B023 — kwargs pertence ao callback

                account_item = upload_item
                if retry_of_session_id is not None:
                    account_item = dict(upload_item)
                    account_item["_retry_of_session_id"] = retry_of_session_id
                return _services.upload_to_account(
                    account_item, account, task_id, config.timeout_blob,
                    config.evaluate, config.finalize,
                    on_progress=_account_progress,
                    unique_video=bool(config.unique_video),
                    session_cache=sessions,
                    recover_pending=not bool(config.recovery_exclusions),
                    recorded_at=scheduled_recorded_at)

            def _safe_send_account(upload_idx: int,
                                   account: AccountSpec,
                                   scheduled_recorded_at: str | None = None,
                                   retry_of_session_id: str | None = None,
                                   ) -> dict[str, Any]:
                """Isola qualquer falha inesperada, inclusive no modo sequencial."""
                try:
                    return _send_account(
                        upload_idx, account, scheduled_recorded_at, retry_of_session_id)
                except Exception as exc:  # noqa: BLE001 — uma conta não mata o lote
                    return {"email": account.email, "org_key": account.org_key,
                            "ok": False,
                            "error": f"{type(exc).__name__}: {exc}"}

            def _send_account_with_recovery(
                upload_idx: int,
                account: AccountSpec,
                scheduled_recorded_at: str | None = None,
            ) -> dict[str, Any]:
                """Repete a sessão inteira até a conta concluir ou a campanha parar."""
                max_attempts = max(1, int(config.account_max_attempts or 1))
                operations_used = 0
                retry_of_session_id: str | None = None
                last_result: dict[str, Any] = {
                    "email": account.email, "org_key": account.org_key,
                    "ok": False, "error": "envio não iniciado",
                }

                def _backoff(kind: str, operation: int) -> bool:
                    delay = min(max(0.0, float(config.account_retry_s))
                                * (2 ** max(0, operation - 2)), 120.0)
                    _emit(kind, email=account.email, attempt=operation,
                          max_attempts=max_attempts, delay_s=delay)
                    return _services._interruptible_sleep(
                        delay, should_stop, _services.partial(_emit, email=account.email),
                        kind=f"{kind}_tick", tick_every=_services._tick_every(delay))

                while operations_used < max_attempts:
                    if should_stop and should_stop():
                        return {**last_result, "stopped": True}
                    if operations_used and _backoff(
                            "account_terminal_retry" if retry_of_session_id else "account_retry",
                            operations_used + 1):
                        return {**last_result, "stopped": True}
                    operations_used += 1
                    last_result = _safe_send_account(
                        upload_idx, account,
                        scheduled_recorded_at if retry_of_session_id is None else None,
                        retry_of_session_id)
                    last_result["campaign_attempts"] = operations_used
                    if last_result.get("ok"):
                        return last_result
                    # Authentication refusals need external attention; an
                    # uncertain write needs reconciliation. Neither permits
                    # another upload or an automatic recovery attempt here.
                    if (last_result.get("authentication_required") is True
                            or last_result.get("review_required") is True):
                        return last_result
                    sid = last_result.get("session_id")
                    if last_result.get("restriction_confirmed") or _services._is_disabled_error(last_result.get("error")):
                        return last_result

                    if (sid and config.cleanup_after_upload and config.evaluate and config.finalize
                            and account.email in sessions):
                        transport_resume_started = False
                        terminal_failure = False
                        while operations_used < max_attempts:
                            if should_stop and should_stop():
                                return {**last_result, "stopped": True}
                            owned = [row for row in _services.list_sidecars()
                                     if row.get("session_id") == sid]
                            if (not owned or not all(
                                    row.get("account_email") == account.email
                                    and row.get("org_key") == account.org_key
                                    and row.get("task_id") == tsk.task_id for row in owned)):
                                break
                            eval_pending = all(
                                _services.is_pending_evaluation(row)
                                and row.get("evaluation_http_status") != 429
                                and "(HTTP 429)" not in str(row.get("error") or "")
                                for row in owned)
                            transport_pending = any(
                                _services.is_pending_transport(row) or _services.is_remote_recovery_candidate(row)
                                for row in owned)
                            post_transport_pending = (transport_resume_started and any(
                                _services.is_pending_evaluation(row) or _services.is_pending_finalization(row)
                                or (row.get("state") in {_services.STATE_COMPLETING, _services.STATE_RETRY_LATE}
                                    and row.get("phase") in {
                                        "transport_done", "completing", "complete",
                                        "awaiting_finalize", "finalize", "finalizing"})
                                for row in owned)
                                and all(row.get("evaluation_http_status") != 429
                                        and "(HTTP 429)" not in str(row.get("error") or "")
                                        for row in owned))
                            if not (transport_pending or eval_pending or post_transport_pending):
                                break
                            evaluation_recovery = eval_pending and not transport_pending
                            if _backoff("account_evaluation_recovery" if evaluation_recovery
                                        else "account_transport_recovery", operations_used + 1):
                                return {**last_result, "stopped": True}
                            operations_used += 1
                            transport_resume_started = True
                            try:
                                def recovery_progress(phase: str, state: str,
                                                      attempt: int, **details: Any) -> None:
                                    _emit("account_progress", clip_uid=clip_info["clip_uid"],
                                          task=tsk.scenario, email=account.email,
                                          phase=phase, state=state, attempt=attempt, **details)
                                _services._pump_account_pending(
                                    sessions[account.email], account.email, account.org_key,
                                    session_ids={sid}, profile=_services.device_profile.get_profile(account.email),
                                    on_progress=recovery_progress)
                                rows = [row for row in _services.list_sidecars()
                                        if row.get("session_id") == sid]
                                recovered = _services._reconcile_uploads(rows, account, item, tsk.task_id)
                                last_result["campaign_attempts"] = operations_used
                                if recovered and recovered.get("ok"):
                                    return {**last_result, **recovered,
                                            ("evaluation_recovered" if evaluation_recovery
                                             else "transport_recovered"): True}
                                if (rows and all(
                                        row.get("account_email") == account.email
                                        and row.get("org_key") == account.org_key
                                        and row.get("task_id") == tsk.task_id for row in rows)
                                        and _services.all_terminal_remote_failure(rows)):
                                    terminal_failure = True
                                    retry_of_session_id = sid
                                    last_result["terminal_failure_session_id"] = sid
                                    last_result["error"] = "A falha terminal do recibo foi confirmada."
                                    break
                                errors = [row.get("error") for row in rows if row.get("error")]
                                if errors:
                                    last_result["error"] = str(errors[0])
                            except Exception as exc:
                                _log(f"  retomada do recibo preservada ({type(exc).__name__})")
                                return last_result
                        if terminal_failure and operations_used < max_attempts:
                            continue
                        if terminal_failure:
                            last_result["error"] = (
                                "Falha terminal confirmada, mas o orçamento de tentativas acabou; "
                                "o recibo e a mídia foram preservados.")
                        return last_result

                    # A receipt without a validated terminal GET remains a
                    # reservation. Never create a second SID for uncertainty.
                    if sid or last_result.get("uploads"):
                        return last_result
                    if last_result.get("retryable") is not True:
                        return last_result
                return last_result

            def _record_account(
                account: AccountSpec,
                acc_res: dict[str, Any],
                results: dict[str, dict[str, Any]] = account_results,
                sent_key: str = registry_key,
                clip_uid: str = clip_info["clip_uid"],
                task_scenario: str = tsk.scenario,
                sends: dict[str, int] = task_sends,
                seconds: dict[str, float] = task_seconds,
                duration_s: float = float(item.get("duration_ms") or 0) / 1000.0,
            ) -> None:
                delivery_media = acc_res.pop("_delivery_media_paths", [])
                previous = results.get(account.email, {})
                if (not acc_res.get("ok")
                        and previous.get("authentication_required") is True
                        and previous.get("session_id") and not acc_res.get("session_id")):
                    # Revalidation or recovery can fail before reading the receipt. That
                    # does not erase the identity saved before suspension.
                    for field in ("session_id", "org_key", "uploads"):
                        if field in previous:
                            acc_res[field] = previous[field]
                if (acc_res.get("ok") and not acc_res.get("skipped")
                        and acc_res.get("finalized") is not True):
                    acc_res = {**acc_res, "ok": False,
                               "error": acc_res.get("error") or
                               "Finalização não confirmada; envio preservado para revisão."}
                # Known pre-effect failures/skips have not consumed this
                # footage. A receipt or an uncertain remote result stays
                # reserved across the remaining tasks in this batch.
                if (acc_res.get("skipped") or (not acc_res.get("ok")
                        and not acc_res.get("review_required")
                        and "retryable" in acc_res and not acc_res.get("session_id")
                        and not acc_res.get("uploads"))):
                    reservation = batch_reservations.pop(account.email, None)
                    if reservation is not None:
                        batch_footage[account.email].remove(reservation)
                results[account.email] = acc_res
                # Persiste antes de atualizar o índice de deduplicação. Uma falha
                # no índice não deve apagar do histórico um envio concluído.
                record_error: Exception | None = None
                try:
                    _persist_item_results()
                except Exception as exc:
                    record_error = exc
                if acc_res.get("authentication_required") is True:
                    # This is a suspended attempt, not a completed failure or
                    # an excluded account. Persist before waiting for attention.
                    if record_error is not None:
                        raise record_error
                    authentication_pending[account.email] = account
                    return
                ok = acc_res.get("ok")
                if ok and not acc_res.get("skipped") and acc_res.get("finalized") is True:
                    try:
                        identities = (_services.sent_registry.delivery_clip_uids(item)
                                      if item.get("source") == "nymeria" else [clip_uid])
                        if len(identities) > 1:
                            _services.sent_registry.mark_sent_many([
                                (sent_key, identity, account.email) for identity in identities])
                        else:
                            _services.sent_registry.mark_sent(sent_key, clip_uid, account.email)
                        if acc_res.get("session_id"):
                            _services._acknowledge_campaign_upload(acc_res["session_id"])
                    except Exception as exc:
                        record_error = record_error or exc
                    if not acc_res.get("skipped"):
                        sends[account.email] = sends.get(account.email, 0) + 1
                        account_seconds[account.email] = (
                            account_seconds.get(account.email, 0.0) + duration_s)
                        seconds[account.email] = (
                            seconds.get(account.email, 0.0) + duration_s)
                ev = acc_res.get("evaluate") or {}
                _log(f"         ok={ok}  finalized={acc_res.get('finalized')} "
                     f"evaluate={ev}" + (f"  err={acc_res.get('error','')[:80]}" if not ok else ""))
                _emit("account_done", clip_uid=clip_uid,
                      task=task_scenario, registry_key=sent_key, email=account.email, ok=ok,
                      finalized=acc_res.get("finalized"),
                      evaluate=ev, error=acc_res.get("error"),
                      skipped=bool(acc_res.get("skipped")),
                      session_id=acc_res.get("session_id"),
                      credited_seconds=(duration_s if ok and not acc_res.get("skipped")
                                        and acc_res.get("finalized") is True else 0.0))
                if not ok and (acc_res.get("restriction_confirmed") or _services._is_disabled_error(acc_res.get("error"))):
                    banned.add(account.email)
                    sessions.pop(account.email, None)
                    acc_res["excluded_from_campaign"] = True
                    _emit("account_excluded", email=account.email)
                elif (not ok and not config.require_all_accounts
                      and acc_res.get("access_error") is True and not acc_res.get("session_id")
                      and not acc_res.get("uploads")):
                    # A classified failure before any receipt may leave this
                    # campaign without archiving/banning the user's account.
                    banned.add(account.email)
                    sessions.pop(account.email, None)
                    acc_res["excluded_from_campaign"] = True
                    _emit("account_deferred", email=account.email, error=acc_res.get("error"))
                if record_error is not None:
                    raise record_error
                if config.cleanup_after_upload and config.unique_video and delivery_media:
                    # Queued accounts can share a persisted device identity.
                    # Protect their exact variant before they publish journals.
                    consumers = {_services.Path(item["video_path"])}
                    for other in pending_accounts:
                        if other.email not in results:
                            consumers.add(_services._account_video_path(
                                _services.Path(item["video_path"]), _services.device_profile.get_profile(other.email)))
                    try:
                        released = _services._cleanup_confirmed_account_media(
                            item, account, acc_res, delivery_media, work_dir,
                            protected_paths=consumers)
                    except (OSError, ValueError, RuntimeError) as exc:
                        _log(f"  armazenamento: variante preservada para revisão ({type(exc).__name__})")
                    else:
                        if released and released["files"]:
                            _emit("storage_cleanup", clip_uid=clip_uid, email=account.email,
                                  files=released["files"], bytes=released["bytes"],
                                  errors=released["errors"], protected=released["skipped"])

            batch_started_at = _services.time.monotonic() if pending_accounts else None
            authentication_pending: dict[str, AccountSpec] = {}
            batch_reservations: dict[str, dict] = {}
            # Reserva TODAS as contas no mesmo instante, antes de disputar
            # vagas de upload. O fim anterior de cada conta continua valendo.
            # A fila ordenada permite enviar contas livres mesmo se outra tem
            # uma reserva futura (por exemplo, após retomar uma campanha).
            batch_epoch = _services.time.time()
            duration_s = float(item.get("duration_ms") or 0) / 1000.0
            queued: list[tuple[float, int, AccountSpec, str | None]] = []
            for upload_idx, account in enumerate(pending_accounts):
                if should_stop and should_stop():
                    break
                ready_at, recorded_at = batch_epoch, None
                if config.realistic_timeline:
                    slot = _services.recording_timeline.reserve(
                        account.email, duration_s, now=batch_epoch)
                    ready_at, recorded_at = slot.end_epoch, slot.recorded_at
                    wait_s = max(0.0, ready_at - _services.time.time())
                    _log(f"      [recording] {account.email}: intervalo "
                         f"{recorded_at} ({duration_s:.0f}s); envio após a gravação")
                    _emit("recording_wait_start", email=account.email,
                          recorded_at=recorded_at,
                          duration_s=duration_s, delay_s=wait_s)
                queued.append((ready_at, upload_idx, account, recorded_at))
            queued.sort(key=lambda entry: (entry[0], entry[1]))
            scheduled_recordings = {account.email: recorded_at for _, _, account, recorded_at in queued}

            if queued:
                # Um JWT fresco por lote (clipe × contas). ADB no celular antes
                # de disparar os workers; o ensure por vídeo continua como rede.
                from .. import appcheck as _appcheck
                _emit("appcheck_batch_renew", clip_uid=clip_info["clip_uid"],
                      task=tsk.scenario, state="start",
                      accounts=len(pending_accounts))
                _log("  App Check: renovando token do celular para este lote…")
                batch_appcheck = {"token_available": False}
                for renew_attempt in range(1, 4):
                    if should_stop and should_stop():
                        break
                    batch_appcheck = _appcheck.renew_app_check_for_batch()
                    if batch_appcheck.get("token_available"):
                        break
                    if renew_attempt >= 3:
                        break
                    wait_s = 15.0 * renew_attempt
                    _log(f"  App Check lote: sem token (tentativa {renew_attempt}/3); "
                         f"nova captura em {wait_s:.0f}s")
                    _emit("appcheck_batch_renew", clip_uid=clip_info["clip_uid"],
                          task=tsk.scenario, state="retry",
                          attempt=renew_attempt, delay_s=wait_s)
                    if _services._interruptible_sleep(
                            wait_s, should_stop, _emit,
                            kind="appcheck_batch_renew_tick",
                            tick_every=_services._tick_every(wait_s)):
                        break
                left_s = (batch_appcheck.get("device") or {}).get("left_s")
                _emit("appcheck_batch_renew", clip_uid=clip_info["clip_uid"],
                      task=tsk.scenario, state="done",
                      token_available=bool(batch_appcheck.get("token_available")),
                      left_s=left_s)
                if batch_appcheck.get("token_available"):
                    _log(f"  App Check lote: token ok"
                         + (f" ({int(left_s)}s restantes)" if isinstance(left_s, (int, float)) and left_s > 0
                            else ""))
                else:
                    diagnostic = batch_appcheck.get("diagnostic") or {}
                    cause = (diagnostic.get("category")
                             or batch_appcheck.get("failure")
                             or "device_capture_failed")
                    message = (diagnostic.get("message")
                               or "App Check indisponível após renovação do lote")
                    _log("  [!] App Check lote indisponível; envio bloqueado antes "
                         "das validações por conta")
                    _emit(
                        "campaign_stopped",
                        reason="app_check_failure",
                        appcheck_cause=cause if isinstance(cause, str) else "device_capture_failed",
                        appcheck_message=message if isinstance(message, str) else str(message),
                    )
                    _stop_warm()
                    _cleanup_on_stop(item, clip_info)
                    break
                if should_stop and should_stop():
                    _log("  [!] campanha interrompida na renovação App Check do lote")
                    _emit("campaign_stopped", reason="parada na renovação App Check do lote")
                    _stop_warm()
                    _cleanup_on_stop(item, clip_info)
                    break

                _log(f"  enviando para {len(pending_accounts)} conta(s) "
                     f"({workers} simultânea(s), teto {_services.max_account_workers()})")
                with _services.ThreadPoolExecutor(max_workers=workers,
                                        thread_name_prefix="moneymin-upload") as pool:
                    futures = {}
                    launched = 0
                    accepting = True
                    authentication_halted = False
                    persistence_error: Exception | None = None
                    next_batch_tick = _services.time.monotonic() + 5.0

                    def _stagger_launch(launched_count: int) -> bool:
                        """Espera o jitter antes da próxima vaga. True = parou."""
                        if config.account_gap_s <= 0 or launched_count <= 0:
                            return False
                        gap = _services.random.uniform(0.6, 1.1) * float(config.account_gap_s)
                        _emit("account_gap_start", delay_s=gap)
                        if _services._interruptible_sleep(
                                gap, should_stop, _emit,
                                kind="account_gap_tick",
                                tick_every=_services._tick_every(gap)):
                            _log("  [!] campanha interrompida no intervalo entre contas")
                            _emit("campaign_stopped",
                                  reason="parada no intervalo entre contas")
                            return True
                        return False

                    while queued or futures:
                        # Colhe todas as conclusões antes de abrir novas vagas:
                        # só a parada solicitada interrompe as demais contas.
                        for future in [f for f in futures if f.done()]:
                            account = futures.pop(future)
                            try:
                                _record_account(account, future.result())
                            except Exception as exc:
                                # Pare novos envios, mas recolha resultados de
                                # workers já ativos antes de encerrar o histórico.
                                persistence_error = persistence_error or exc
                                accepting = False
                                queued.clear()
                        if should_stop and should_stop():
                            accepting = False
                        while accepting and not authentication_pending and queued and len(futures) < workers:
                            # A espera de gravação pode terminar depois que a
                            # janela fechou; não inicie PUT fora do horário.
                            if (config.active_hours
                                    and _services._window_remaining_s(config.active_hours) > 0):
                                break
                            if queued[0][0] > _services.time.time():
                                break
                            if _stagger_launch(launched) or (should_stop and should_stop()):
                                accepting = False
                                break
                            _ready_at, idx, account, recorded_at = queued.pop(0)
                            if account.email not in batch_reservations:
                                batch_reservations[account.email] = _reserve_batch_footage(account.email, clip_info)
                            future = pool.submit(
                                _send_account_with_recovery, idx, account, recorded_at)
                            futures[future] = account
                            launched += 1
                        if not futures:
                            if accepting and authentication_pending:
                                if any(account_results[email].get("review_required") is True
                                       for email in authentication_pending):
                                    accepting = False
                                    authentication_halted = True
                                    queued.clear()
                                    _emit("campaign_stopped", reason="authentication_review")
                                    break
                                app_check_pending = [
                                    email for email in authentication_pending
                                    if (account_results[email].get("blocked_reason") == "app_check"
                                        or account_results[email].get("appcheck_cause"))
                                ]
                                if app_check_pending:
                                    sample = account_results[app_check_pending[0]]
                                    accepting = False
                                    authentication_halted = True
                                    queued.clear()
                                    _emit(
                                        "campaign_stopped",
                                        reason="app_check_failure",
                                        appcheck_cause=sample.get("appcheck_cause") or "unknown",
                                        appcheck_message=(
                                            sample.get("appcheck_message")
                                            or sample.get("error")
                                            or "App Check indisponível"),
                                    )
                                    break
                                # All active results have been committed. Only
                                # the coordinator waits; no worker holds a
                                # journal/transport lease while the user acts.
                                resumed = _emit("authentication_wait",
                                                accounts=sorted(authentication_pending))
                                if resumed is not True or (should_stop and should_stop()):
                                    accepting = False
                                    authentication_halted = True
                                    queued.clear()
                                    _emit("campaign_stopped", reason="autenticação pendente")
                                    break
                                for email, account in authentication_pending.items():
                                    # A fresh session observes credentials that
                                    # were corrected externally. Existing receipt
                                    # reconciliation still forbids a second SID.
                                    sessions.pop(email, None)
                                    idx = pending_accounts.index(account)
                                    queued.append((_services.time.time(), idx, account,
                                                   scheduled_recordings.get(email)))
                                authentication_pending.clear()
                                queued.sort(key=lambda entry: (entry[0], entry[1]))
                                continue
                            if not accepting or not queued:
                                break
                            if config.active_hours and _services._window_remaining_s(config.active_hours) > 0:
                                if _services._wait_for_window(config.active_hours, should_stop, _emit):
                                    accepting = False
                                continue
                            # Nenhum worker fica dormindo pela gravação. Espera
                            # só até a próxima conta ficar pronta, não a última.
                            wait_s = max(0.0, queued[0][0] - _services.time.time())
                            if _services._interruptible_sleep(
                                    wait_s, should_stop,
                                    _services.partial(_emit, email=queued[0][2].email,
                                            pending_accounts=len(queued)),
                                    kind="recording_wait_tick",
                                    tick_every=_services._tick_every(wait_s)):
                                accepting = False
                            continue
                        timeout = 0.5
                        if accepting and queued and len(futures) < workers:
                            timeout = min(timeout, max(0.0, queued[0][0] - _services.time.time()))
                        _services.wait(futures, timeout=timeout, return_when=_services.FIRST_COMPLETED)
                        if _services.time.monotonic() >= next_batch_tick:
                            _emit("batch_tick", clip_uid=clip_info["clip_uid"],
                                  pending_accounts=len(futures),
                                  elapsed_s=int(_services.time.monotonic() - batch_started_at))
                            next_batch_tick = _services.time.monotonic() + 5.0
                    if persistence_error is not None:
                        _stop_warm()
                        raise persistence_error
                    if authentication_halted:
                        _stop_warm()
                        log.save()
                        return log

            item["accounts"] = [account_results[a.email] for a in config.accounts
                                if a.email in account_results]
            sent_now = any(
                result.get("ok") and not result.get("skipped")
                for result in account_results.values()
            )
            if sent_now:
                last_dur_s = item["duration_ms"] / 1000
                last_batch_started_at = batch_started_at
                delay_pending = True
            # Salva inclusive um lote parcial interrompido: os envios que já
            # terminaram não desaparecem do histórico nem da retomada.
            if account_results:
                _persist_item_results()
            if should_stop and should_stop():
                if account_results:
                    _emit("item_done", clip_uid=clip_info["clip_uid"],
                          task=tsk.scenario, partial=True)
                _log("  [!] campanha interrompida pelo usuário")
                _emit("campaign_stopped", reason="parada pelo usuário")
                _stop_warm()
                _cleanup_on_stop(item, clip_info)
                break
            retained_accounts = [a for a in pending_accounts
                                 if not account_results.get(a.email, {}).get("excluded_from_campaign")]
            all_pending_succeeded = not retained_accounts or _services._all_pending_uploads_succeeded(
                retained_accounts, account_results)
            # Não espera tarefa de fundo: todas as variantes usadas por contas
            # bem-sucedidas já terminaram; o prefetch do próximo segue ativo.
            _stop_warm()
            failed_accounts = [
                result for result in account_results.values()
                if not result.get("ok") and not result.get("skipped")
                and (not result.get("excluded_from_campaign")
                     or result.get("session_id") or result.get("uploads"))
            ]
            if (config.require_all_accounts or config.cleanup_after_upload) and failed_accounts:
                details = "; ".join(
                    f"{result.get('email')}: {result.get('error') or 'erro desconhecido'}"
                    for result in failed_accounts
                )
                _emit("item_incomplete", clip_uid=clip_info["clip_uid"],
                      task=tsk.scenario,
                      accounts=[r.get("email") for r in failed_accounts])
                raise RuntimeError(
                    "lote incompleto após todas as tentativas; "
                    "a campanha preservou a mídia e não adquiriu outro vídeo. "
                    "Contas pendentes: " + details
                )
            if pending_accounts:
                if reserved and not config.cleanup_after_upload:
                    _log("  armazenamento: mídia preservada para a recuperação do envio anterior")
                elif config.cleanup_after_upload and all_pending_succeeded:
                    from .. import recovery
                    reconciled = recovery.reconcile_confirmed(refresh=False)
                    if reconciled.get("archive_cleanup_errors"):
                        raise RuntimeError(
                            "Limpeza incompleta dos ZIPs de tentativas substituídas; "
                            "os envios confirmados e os diagnósticos foram preservados. "
                            "A campanha não adquiriu outro vídeo.")
                    cleanup = _services._cleanup_uploaded_item(
                        item,
                        work_dir,
                        protected_paths=prefetch.protected_paths(),
                    )
                    cleanup["files"] += int(reconciled.get("archives_removed") or 0)
                    cleanup["bytes"] += int(reconciled.get("archive_bytes_removed") or 0)
                    _emit(
                        "storage_cleanup",
                        clip_uid=clip_info["clip_uid"],
                        files=cleanup["files"],
                        bytes=cleanup["bytes"],
                        errors=cleanup["errors"],
                        protected=cleanup["protected"],
                        retained_managed=cleanup.get("retained_managed", 0),
                        retained_bytes=cleanup.get("retained_bytes", 0),
                    )
                    _log(
                        "  armazenamento: removeu "
                        f"{cleanup['files']} arquivo(s), "
                        f"{cleanup['bytes'] / (1024 ** 2):.1f} MB"
                        + (f"; {cleanup['protected']} arquivo(s) em uso pelo próximo preparo"
                           if cleanup["protected"] else "")
                        + (f"; falhas: {len(cleanup['errors'])}"
                           if cleanup["errors"] else "")
                    )
                    if cleanup["errors"]:
                        _emit("item_incomplete", clip_uid=clip_info["clip_uid"],
                              task=tsk.scenario, reason="cleanup_failed",
                              errors=list(cleanup["errors"]))
                        raise RuntimeError(
                            "Limpeza incompleta após falhas de remoção dos envios confirmados; "
                            "a campanha não adquiriu outro vídeo. Resolva a pendência e retome.")
                    if cleanup.get("retained_managed", 0):
                        _note_retained_media(cleanup, clip_info, tsk)
                elif config.cleanup_after_upload and not all_pending_succeeded:
                    _log(
                        "  armazenamento: mídia mantida porque há conta "
                        "pendente ou envio com falha"
                    )
                else:
                    removed, freed = _services._enforce_account_video_cache(work_dir)
                    if removed:
                        _log(f"  cache de variantes: liberou {removed} arquivo(s), "
                             f"{freed / (1024 ** 3):.1f} GB")
                _emit("item_done", clip_uid=clip_info["clip_uid"],
                      task=tsk.scenario, partial=False)
            elif config.cleanup_after_upload:
                cleanup = _services._cleanup_uploaded_item(item, work_dir)
                if cleanup["errors"]:
                    raise RuntimeError(
                        "Limpeza incompleta após falhas de remoção dos envios confirmados; "
                        "a campanha não adquiriu outro vídeo. Resolva a pendência e retome.")
                if cleanup.get("retained_managed", 0):
                    _note_retained_media(cleanup, clip_info, tsk)
            # (log: sem os blobs/csv brutos — grandes; identity fica por conta)

        if log.status != "stopped" and not quota_s and not until_exhausted and automatic_selection:
            remaining = {a.email: tsk.count - task_sends.get(a.email, 0)
                         for a in config.accounts if task_sends.get(a.email, 0) < tsk.count}
            if remaining:
                _emit("task_shortfall", task_name=display_name, task_id=tsk.task_id,
                      requested_per_account=tsk.count, remaining_sends=remaining)
        if len(banned) == len(config.accounts):
            break
        if quota_s and all(account_seconds.get(a.email, 0) >= quota_s
                           for a in config.accounts):
            _log(f"  [i] meta de {quota_s / 3600:.1f}h por conta atingida")
            break

    if log.status != "stopped":
        if quota_s:
            remaining = {a.email: max(0.0, quota_s - account_seconds.get(a.email, 0))
                         for a in config.accounts if account_seconds.get(a.email, 0) < quota_s}
            if remaining:
                _emit("goal_shortfall", target_seconds=quota_s, remaining_seconds=remaining)
        log.status = ("error" if not sends["ok"] and not sends["skipped"] else
                      "partial" if any(issue.get("kind") not in {"authentication_wait", "storage_retained"}
                                       for issue in log.issues)
                      or sends["failed"] else "done")
    log_path = log.save()
    _log(f"\nlog salvo: {log_path.name}")
    if log.status != "stopped":
        # Só o nome do arquivo vai para a UI — nada de caminhos absolutos.
        _emit("campaign_done", log_path=log_path.name, status=log.status,
              ok_sends=sends["ok"], failed_sends=sends["failed"],
              skipped_sends=sends["skipped"],
              issues=sum(issue.get("kind") not in {"authentication_wait", "storage_retained"}
                          for issue in log.issues),
              shortfall_accounts=sum(account_seconds.get(a.email, 0) < quota_s
                                     for a in config.accounts) if quota_s else 0,
              preparation_failures=sum(issue.get("kind") == "clip_prepare_done"
                                       for issue in log.issues))
    return log

REQUIRES = ('CampaignLog', 'FIRST_COMPLETED', 'Path', 'STATE_COMPLETING', 'STATE_RETRY_LATE', 'ThreadPoolExecutor', '_account_video_path', '_acknowledge_campaign_upload', '_all_pending_uploads_succeeded', '_batch_footage_overlaps', '_candidate_reserved_emails', '_candidate_sent_emails', '_cleanup_confirmed_account_media', '_cleanup_rejected_prepare', '_cleanup_stopped_prepared_media', '_cleanup_uploaded_item', '_clip_is_cached', '_compatible_task_clips', '_ego_clip_inputs', '_enforce_account_video_cache', '_interruptible_sleep', '_is_disabled_error', '_prefer_cached_clips', '_prefetch_following', '_pump_account_pending', '_reconcile_uploads', '_recorded_at_now', '_tick_every', '_try_prepare_imu_carve', '_wait_for_window', '_warm_account_videos', '_window_remaining_s', 'all_terminal_remote_failure', 'automatic_candidates', 'clamp_account_workers', 'device_profile', 'diverse_order', 'diversity_summary', 'ego4d', 'holoassist', 'imu_rejections', 'is_pending_evaluation', 'is_pending_finalization', 'is_pending_transport', 'is_remote_recovery_candidate', 'list_sidecars', 'math', 'max_account_workers', 'normalize_content_mode', 'normalize_dataset_provider', 'nymeria', 'parent_key', 'partial', 'prepare_clip', 'prepare_holoassist_clip', 'prepare_nymeria_clip', 'random', 'recording_timeline', 'sent_registry', 'task_matching', 'time', 'upload_to_account', 'wait')
