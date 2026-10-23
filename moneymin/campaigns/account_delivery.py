"""Account upload transaction, retry policy and session delivery coordination."""
from __future__ import annotations

import math
from .execution import safe_account_diagnostics, safe_campaign_error, sanitize_account_error

def upload_to_account(_services, item: dict[str, Any], account: AccountSpec,
                      task_id: str, timeout_blob: int,
                      evaluate: bool, finalize: bool,
                      on_progress: Callable[..., None] | None = None,
                      unique_video: bool = False,
                      session: Session | None = None,
                      session_cache: dict[str, Session] | None = None,
                      recorded_at: str | None = None,
                      recover_pending: bool = True,
                      **_legacy: Any,
                      ) -> dict[str, Any]:
    """Sobe um item JÁ PREPARADO para uma conta (o MP4 não é refeito).

    session_id é por conta (senão o Azure colide a chave). O arquivo de vídeo
    é o `_native.mp4` compartilhado, salvo `unique_video=True`.
    """
    # Provenance revalidation may attach a private fingerprint memo to its
    # input. Keep that transaction-local cache off the prepared item owned by
    # the caller and shared with other account workers.
    item = dict(item)
    result: dict[str, Any] = {"email": account.email, "ok": False}
    if item.get("requires_audio") is True:
        # Probe the delivered file, not a cached claim. Nymeria's RGB extractor
        # currently produces no audio; never substitute a silent/synthetic track.
        if _services.probe_video(item["video_path"]).get("has_audio") is not True:
            result.update(finalized=False, retryable=False,
                          error="Esta tarefa exige áudio real; o vídeo preparado não possui áudio.")
            return result
    if item.get("source") in {"ego4d", "nymeria"} and item.get("imu_real") is not True:
        result.update(finalized=False, retryable=False,
                      error="Dataset sem sensores reais confirmados no preparo; envio bloqueado.")
        return result
    try:
        if item.get("source") == "nymeria":
            from .. import content_provenance
            if not item.get("imu_csv") or not item.get("frames_csv"):
                raise _services.UploadError(
                    "Nymeria sem CSV medido completo; envio bloqueado sem substituição sintética.",
                    transient=False, phase="prepare")
            try:
                result["source_provenance"] = content_provenance.revalidate_content_provenance(item)
                if (_services.Path(item["video_path"]).resolve()
                        != _services.Path(item["_content_inputs"]["prepared_video"]["path"]).resolve()):
                    raise ValueError(content_provenance.INTEGRITY_ERROR)
                candidate = dict(item["_content_candidate"])
                candidate["selection_evidence"] = item["content_provenance"]["selection_evidence"]
                result["selection_evidence"] = _services.nymeria.revalidate_candidate(
                    candidate, task_name=item.get("task_name_authoritative"),
                    task_id=task_id, registry_key=item.get("registry_key"))["selection_evidence"]
                measured_csv, measured_stats = _services._nymeria_resample_delivery(item)
                if (measured_csv != item["imu_csv"]
                        or measured_stats != item.get("imu_diagnostics")
                        or measured_stats["sampleCount"] != item.get("n_samples")):
                    raise ValueError(content_provenance.INTEGRITY_ERROR)
            except (KeyError, TypeError, ValueError, RuntimeError, OSError) as exc:
                raise _services.UploadError("Prova Nymeria ausente ou alterada; novo preparo necessário.",
                                  transient=False, phase="prepare") from exc
        if item.get("source") == "ego4d":
            from .. import content_provenance
            try:
                if not item.get("imu_csv") or not item.get("frames_csv"):
                    raise ValueError("Ego4D sem CSV medido completo; envio bloqueado sem substituição sintética.")
                result["source_provenance"] = content_provenance.revalidate_content_provenance(item)
                if not isinstance(item.get("_content_candidate"), dict):
                    raise ValueError(content_provenance.INTEGRITY_ERROR)
                candidate = dict(item["_content_candidate"])
                candidate["selection_evidence"] = item["content_provenance"]["selection_evidence"]
                result["selection_evidence"] = _services.ego4d.revalidate_selection_evidence(
                    candidate, task_name=item.get("task_name_authoritative"),
                    task_id=task_id, registry_key=item.get("registry_key"))
            except ValueError as exc:
                raise _services.UploadError(str(exc), transient=False, phase="prepare") from exc
        if (_services.org_policy.account_kind(account.email) == "crowtado"
                and account.org_key != _services.config.ORG_KEY):
            raise _services.AuthError(
                "Organização de destino incompatível com o vínculo da conta.",
                code="organization",
            )
        sess = session
        if sess is None and session_cache is not None:
            sess = session_cache.get(account.email)
        if sess is None:
            with _services.account_registration_route(account.email):
                sess = _services.Session.from_email(account.email)
                _services._bind_session_email(sess, account.email)
                # _live only means the Firebase token was refreshed. It does not
                # prove that Minute permits this account/organization to upload.
                sess.ensure_auth(org_key=account.org_key)
            if session_cache is not None:
                session_cache[account.email] = sess
        else:
            _services._bind_session_email(sess, account.email)
            with _services.account_registration_route(account.email):
                if not getattr(sess, "_live", False):
                    # Gate de acesso/organização e versão; qualidade não autentica.
                    sess.ensure_auth(org_key=account.org_key)
                else:
                    sess.warmup()
        # Campanhas longas ultrapassam a vida do JWT. Renove abaixo do piso
        # (~10 min) antes deste vídeo; soft ADB + reinício do Minute se preciso.
        from .. import appcheck
        if on_progress:
            on_progress("appcheck", "renew", 0)
        appcheck_status = appcheck.ensure_app_check_fresh()
        if on_progress:
            on_progress("appcheck", "renew", 1)
        if not appcheck_status.get("token_available"):
            diagnostic = appcheck_status.get("diagnostic") or {}
            cause = diagnostic.get("category") or appcheck_status.get("failure") or "device_capture_failed"
            cause = safe_account_diagnostics({"appcheck_cause": cause}).get(
                "appcheck_cause", "device_capture_failed")
            raise _services.UploadError(
                "App Check indisponível após tentativa de renovação no celular. "
                "Resolva a indisponibilidade de App Check antes de retomar o envio.",
                transient=False,
                phase="prepare",
                blocked_reason="app_check",
                review_required=False,
                appcheck_cause=cause,
                request_not_sent=True,
            )
        result["org_key"] = account.org_key
        profile = _services.device_profile.get_profile(account.email)
        if recover_pending and not getattr(sess, "_moneymin_pending_pumped", False):
            recovered = _services._pump_account_pending(
                sess, account.email, account.org_key, on_progress=on_progress)
            sess._moneymin_pending_pumped = True
            if recovered:
                result["recovered_uploads"] = len(recovered)
        else:
            recovered = []
        # A reviewed reservation list can omit the global pump. It cannot
        # replace the authoritative receipts when linking a terminal retry or
        # checking whether a newer session already consumed this content.
        journals = _services.list_sidecars()
        # pump_pending returns journals that the next disk read usually also
        # contains. Remove only identical overlap between those two sources;
        # conflicting duplicates within persisted data must remain detectable.
        reconciliation_rows = [*journals, *(row for row in recovered if row not in journals)]
        reconciliation = _services._reconcile_uploads(reconciliation_rows, account, item, task_id)
        if reconciliation is not None:
            return sanitize_account_error(reconciliation)
        retry_parent = _services._terminal_retry_parent(reconciliation_rows, account, item, task_id)
        requested_parent = item.get("_retry_of_session_id")
        if requested_parent is not None:
            if (not isinstance(requested_parent, str) or not requested_parent
                    or retry_parent != requested_parent):
                raise _services.UploadError(
                    "A tentativa terminal de origem não corresponde aos recibos validados.",
                    transient=False, phase="recovery", review_required=True)
        if retry_parent is not None:
            item = dict(item)
            item["_retry_of_session_id"] = retry_parent
        policy_limits = (
            sess.recording_policy.limits()
            if getattr(sess, "recording_policy", None) is not None
            else _services.config.recording_limits())
        dur_s = item["duration_ms"] / 1000
        session_id, log_id, recorded_at = _services._new_identity(
            dur_s, account.email, recorded_at=recorded_at, limits=policy_limits)
        base_video = _services.Path(item["video_path"])
        if unique_video:
            if on_progress:
                on_progress("encode", "start", 1)
            video_path = (_services._per_account_video(base_video, profile, preserve_pts=True)
                          if item.get("source") == "nymeria"
                          else _services._per_account_video(base_video, profile))
            if on_progress:
                on_progress("encode", "done", 1)
        else:
            video_path = base_video
        video_probe = item.get("probe") if not unique_video else None
        if not video_probe:
            video_probe = _services.probe_video(video_path)
        if item.get("requires_audio") is True and video_probe.get("has_audio") is not True:
            raise _services.UploadError("Esta tarefa exige áudio real; o vídeo de entrega não possui áudio.",
                              transient=False, phase="prepare")
        imu_csv = item.get("imu_csv") or ""
        if unique_video and item.get("imu_path") and item.get("window_s"):
            imu_diag_live: dict[str, Any] = {}
            imu_csv = _services.ego4d.build_imu_csv(
                item["imu_path"], tuple(item["window_s"]),
                duration_ms=item["duration_ms"],
                seed=f"{item['clip_uid']}|{account.email}",
                stats=imu_diag_live)
            item = dict(item)
            item["imu_diagnostics"] = imu_diag_live
        fps = video_probe.get("fps") or 30.0
        start_wall = _services.device_profile.recorded_at_to_wall_ms(recorded_at)
        wall_for_uptime = start_wall or int(_services.time.time() * 1000)
        from ..mobile import physical_binding as pb
        from ..mobile.physical_binding import PhysicalDeviceRequiredError
        try:
            if pb.device_mode_requires_physical():
                bound = pb.require_campaign_physical_binding(
                    wall_ms=wall_for_uptime)
                frames_offset = bound["uptime_ns"]
                frames_gop = bound["frames_gop"]
                imu_device_id = bound["device_id"]
            else:
                frames_offset = profile.uptime_ns_at(wall_for_uptime)
                frames_gop = profile.frames_gop
                imu_device_id = profile.device_id
        except PhysicalDeviceRequiredError as exc:
            raise _services.UploadError(
                str(exc), transient=False, phase="prepare") from exc
        if not imu_csv:
            if item.get("source") in {"ego4d", "nymeria"}:
                raise _services.UploadError(
                    "IMU real do dataset ausente; envio bloqueado sem substituição sintética.",
                    transient=False, phase="prepare")
            # Sem IMU real: sinal do aparelho (físico em modo device; perfil fora).
            imu_csv = _services.build_imu_csv(
                int(item["duration_ms"]),
                seed=f"moneymin.imu:{imu_device_id}")
        require_pts = item.get("source") in {"ego4d", "nymeria"}
        # frames.csv SEMPRE derivado do MP4 REAL (PTS/keyframes) + offset do
        # uptime Android do 1º frame. Nunca reusar o CSV preparado com relógio
        # em zero (desync: o metadata usa firstFrameSensorTimestampNs = uptime).
        # Em modo device offset/GOP vêm do celular físico conectado.
        try:
            frames_csv = _services.build_frames_csv_from_video(
                video_path, duration_ms=int(item["duration_ms"]),
                fps=fps, gop=frames_gop, offset_ns=frames_offset,
                **({"require_measured_pts": True} if require_pts else {}))
        except ValueError as exc:
            if not require_pts:
                raise
            raise _services.UploadError(
                'Vídeo sem PTS medidos; preparo interrompido.',
                transient=False, phase='prepare') from exc
        plan = _services._chunk_plan(int(item["duration_ms"]), limits=policy_limits)
        chunk_imu_diagnostics: list[dict[str, Any]] | None = None
        if len(plan) > 1 and item.get("source") == "ego4d":
            # Classify the same source resampling events on each half-open
            # output interval. Resampling a chunk in isolation changes edge
            # holds/interpolation; copying the full item's counters repeats
            # events that do not belong to this ZIP.
            try:
                source_imu = item["_content_inputs"]["source_imu"]
                measured: dict[str, Any] = {}
                measured_csv = _services.ego4d.build_imu_csv(
                    source_imu["path"], tuple(item["content_provenance"]["window_s"]),
                    duration_ms=int(item["duration_ms"]), stats=measured,
                    stats_windows_ms=[(start, start + duration) for start, duration in plan])
                content_provenance.verify_input(source_imu)
                if measured_csv != imu_csv:
                    raise ValueError(content_provenance.INTEGRITY_ERROR)
                chunk_imu_diagnostics = measured["windows"]
            except (KeyError, TypeError, ValueError, RuntimeError, OSError) as exc:
                raise _services.UploadError(
                    "IMU ou diagnóstico do chunk divergente; novo preparo necessário.",
                    transient=False, phase="prepare") from exc
        chunk_paths: list[_services.Path] = []
        chunk_zips: list[bytes] = []
        chunk_recorded: list[str] = []
        lineage_chunks: list[dict[str, Any]] = []
        for index, (start_ms, dur_ms) in enumerate(plan):
            log_id = f"{session_id}_{index}"
            rec_at = recorded_at
            if start_wall is not None:
                rec_at = _services.device_profile.format_recorded_at(
                    start_wall / 1000.0 + start_ms / 1000.0)
            if len(plan) == 1:
                part = video_path
                part_imu, n_imu = imu_csv, int(item.get("n_samples") or 0)
                part_frames = frames_csv
            else:
                if item.get("source") == "nymeria":
                    part = _services._chunk_video_path(video_path, index, start_ms, dur_ms, preserve_pts=True)
                    _services._cut_video_chunk(video_path, part, start_ms / 1000.0, dur_ms / 1000.0,
                                     preserve_pts=True)
                else:
                    part = _services._chunk_video_path(video_path, index, start_ms, dur_ms)
                    _services._cut_video_chunk(video_path, part, start_ms / 1000.0, dur_ms / 1000.0)
                part_imu, n_imu = _services._slice_imu_csv(imu_csv, start_ms, dur_ms)
                try:
                    chunk_wall = (
                        _services.device_profile.recorded_at_to_wall_ms(rec_at)
                        or start_wall or int(_services.time.time() * 1000))
                    if pb.device_mode_requires_physical():
                        chunk_bound = pb.require_campaign_physical_binding(
                            wall_ms=chunk_wall)
                        chunk_offset = chunk_bound["uptime_ns"]
                        chunk_gop = chunk_bound["frames_gop"]
                    else:
                        chunk_offset = profile.uptime_ns_at(chunk_wall)
                        chunk_gop = profile.frames_gop
                    part_frames = _services.build_frames_csv_from_video(
                        part, duration_ms=dur_ms, fps=fps,
                        gop=chunk_gop,
                        offset_ns=chunk_offset,
                        **({"require_measured_pts": True}
                           if require_pts else {}))
                except PhysicalDeviceRequiredError as exc:
                    raise _services.UploadError(
                        str(exc), transient=False, phase="prepare") from exc
                except ValueError as exc:
                    if not require_pts:
                        raise
                    raise _services.UploadError(
                        'Vídeo sem PTS medidos; preparo interrompido.',
                        transient=False, phase='prepare') from exc
            part_probe = _services.probe_video(part) if len(plan) > 1 else video_probe
            chunk_item = dict(item)
            if len(plan) > 1 and item.get("source") == "nymeria":
                # A cut begins at its first captured RGB frame, which may be
                # later than a policy boundary. Resample this part at that
                # actual capture origin instead of shifting the whole CSV.
                from bisect import bisect_left
                source_pts = _services._measured_video_pts(video_path)
                first_index = bisect_left(source_pts, start_ms * 1_000_000)
                dur_ms = int(part_probe.get("duration_ms") or 0)
                minimum = max(1000, int(policy_limits.get("min_duration_ms") or _services.MIN_DUR_MS))
                maximum = max(minimum, int(policy_limits.get("max_duration_ms") or _services.MAX_DUR_MS))
                if not minimum <= dur_ms <= maximum:
                    raise _services.UploadError("Duração medida do chunk Nymeria fora da política.",
                                      transient=False, phase="prepare")
                if first_index >= len(source_pts):
                    raise _services.UploadError("Chunk Nymeria sem primeiro frame medido.", transient=False, phase="prepare")
                source_origin = item["_nymeria_resample_inputs"]["origin_ns"] + source_pts[first_index]
                part_imu, part_stats = _services._nymeria_resample_delivery(
                    item, origin_ns=source_origin, duration_ms=dur_ms)
                n_imu = int(part_stats["sampleCount"])
                chunk_item["imu_diagnostics"] = part_stats
                chunk_item["n_samples"] = n_imu
            if len(plan) > 1:
                chunk_item["n_samples"] = n_imu
                if chunk_imu_diagnostics is not None:
                    if chunk_imu_diagnostics[index]["sampleCount"] != n_imu:
                        raise _services.UploadError(
                            "Diagnóstico IMU diverge das amostras do chunk; novo preparo necessário.",
                            transient=False, phase="prepare")
                    chunk_item["imu_diagnostics"] = chunk_imu_diagnostics[index]
            chunk_zips.append(_services._build_sidecar(
                chunk_item, session_id, log_id, rec_at, profile=profile,
                video_probe=part_probe, imu_csv=part_imu,
                frames_csv=part_frames, chunk_index=index,
                duration_ms=dur_ms))
            chunk_paths.append(part)
            chunk_recorded.append(rec_at)
            if item.get("source") in {"ego4d", "nymeria"}:
                lineage_chunks.append({"index": index, "start_ms": start_ms, "duration_ms": dur_ms,
                    "video_path": str(part), "imu_csv": part_imu, "frames_csv": part_frames,
                    "sidecar_bytes": chunk_zips[-1], "recorded_at": rec_at})
        result["session_id"] = session_id
        # The coordinator consumes this private carrier before persisting the
        # account result. It permits per-account eviction after the journals
        # and immutable receipt have been published, without guessing filenames.
        result["_delivery_media_paths"] = [str(video_path), *(str(path) for path in chunk_paths)]
        if item.get("source") in {"ego4d", "nymeria"}:
            try:
                result["source_provenance"] = content_provenance.bind_content_delivery(
                    item, session_id, task_id, account.org_key, lineage_chunks)
            except ValueError as exc:
                raise _services.UploadError(str(exc), transient=False, phase="prepare") from exc
        try:
            res = None
            with _services.account_registration_route(account.email):
                res = _services.upload_session(
                    sess, chunk_paths if len(chunk_paths) > 1 else chunk_paths[0],
                    account.org_key,
                    task_id=task_id, session_id=session_id,
                    recorded_at=chunk_recorded if len(chunk_recorded) > 1 else recorded_at,
                    sidecar=True,
                    sidecar_data=chunk_zips if len(chunk_zips) > 1 else chunk_zips[0],
                    normalize=False, register_first=True,
                    persist_sidecar=True,
                    evaluate=evaluate, finalize=finalize,
                    timeout_blob=timeout_blob,
                    profile=profile,
                    suppress_per_chunk_catbear=True,
                    on_progress=on_progress,
                    campaign_context={"registry_key": item["registry_key"], "clip_uid": item["clip_uid"],
                                      "task_id": task_id,
                                      **({"retry_of_session_id": item["_retry_of_session_id"]}
                                         if isinstance(item.get("_retry_of_session_id"), str)
                                         else {}),
                                      **({"history_name": item['_history_name']}
                                         if item.get('_history_name') else {}),
                                      **({"content_provenance": result["source_provenance"]}
                                         if item.get("source") in {"ego4d", "nymeria"} else {})}
                                      if item.get("registry_key") and item.get("clip_uid") else None,
                    **({"expected_video_sha256": [row["video"]["sha256"]
                                                   for row in result["source_provenance"]["chunks"]]}
                       if item.get("source") in {"ego4d", "nymeria"} else {}),
                )
        except _services._AccountRouteError as exc:
            if exc.route_phase == "setup":
                # Nothing entered the Minute upload operation. Permit the
                # existing bounded retry to use a fresh session identity.
                result.pop("session_id", None)
                raise
            if res is None:
                raise
            # The remote call already returned its receipt. A teardown error
            # cannot undo a finalized delivery or justify creating a new SID.
            result["warning"] = (
                "A operação remota retornou; não foi possível encerrar a rota atribuída."
            )
        result["session_id"] = res.session_id
        result["finalized"] = res.finalized
        result["finalize_status"] = safe_account_diagnostics(
            {"http_status": res.finalize_status}).get("http_status")
        if (getattr(res, "authentication_required", False) is True
                or any(getattr(chunk, "authentication_required", False) is True for chunk in res.chunks)):
            result["authentication_required"] = True
            result["retryable"] = False
        if (getattr(res, "review_required", False) is True
                or any(getattr(chunk, "review_required", False) is True for chunk in res.chunks)):
            result["review_required"] = True
            result["retryable"] = False
        appcheck_chunk = next(
            (chunk for chunk in res.chunks
             if getattr(chunk, "appcheck_cause", None)
             or (isinstance(getattr(chunk, "blocked_reason", None), str)
                 and chunk.blocked_reason.casefold() == "app_check")),
            None)
        if appcheck_chunk is not None:
            blocked = getattr(appcheck_chunk, "blocked_reason", None)
            if isinstance(blocked, str):
                result["blocked_reason"] = blocked.casefold()
            if getattr(appcheck_chunk, "appcheck_cause", None):
                result["appcheck_cause"] = appcheck_chunk.appcheck_cause
        elif getattr(res, "appcheck_cause", None) or getattr(res, "blocked_reason", None):
            blocked = getattr(res, "blocked_reason", None)
            if isinstance(blocked, str):
                result["blocked_reason"] = blocked.casefold()
            if getattr(res, "appcheck_cause", None):
                result["appcheck_cause"] = res.appcheck_cause
        if (result.get("authentication_required") is True
                and any(getattr(chunk, "authentication_required", False) is True
                        and not chunk.upload_id
                        and getattr(chunk, "request_not_sent", False) is not True
                        for chunk in res.chunks)):
            # A diagnostic category is not proof that no CREATE was sent.
            # Every receipt-less attempt needs its own explicit local evidence.
            result["review_required"] = True
        chunks_ok = bool(res.chunks) and all(c.state == "done" for c in res.chunks)
        finalize_ok = not finalize or res.finalized
        # Um blob completo sem finalize não aparece como sessão entregue. Nunca
        # registre esse estado intermediário como sucesso da conta.
        result["ok"] = chunks_ok and finalize_ok
        # consolida evaluate
        counts: dict[str, int] = {}
        fails: list[str] = []
        for c in res.chunks:
            ev = c.evaluate_result or {}
            for chk in ev.get("checks") or []:
                st = chk.get("status", "?")
                counts[st] = counts.get(st, 0) + 1
                if st == "fail":
                    fails.append(chk.get("id", "?"))
        result["evaluate"] = counts
        result["evaluate_fails"] = fails
        result["uploads"] = [c.upload_id for c in res.chunks]
        # Falhas de transporte/create/complete são devolvidas como ChunkResult,
        # não como exceção. Preserve a causa no log da campanha em vez de gravar
        # apenas ok=false com um upload_id vazio.
        chunk_errors = [c.error for c in res.chunks if c.error]
        if chunk_errors:
            # Legacy chunk.error may contain credentials or an entire response.
            # Preserve typed facts instead; text is never restriction evidence.
            for chunk in res.chunks:
                if chunk.error:
                    facts = safe_account_diagnostics({
                        key: getattr(chunk, key, None) for key in (
                            "account_issue_code", "http_status", "account_issue_status", "phase",
                            "blocked_reason", "appcheck_cause", "retry_after", "retry_after_seconds")})
                    # Aggregate policy flags above are authoritative for the
                    # entire session. A later chunk must not clear a pause or
                    # uncertainty flag, or claim no request for other chunks.
                    result.update(facts)
                    if facts.get("account_issue_code") == "restricted":
                        result["restriction_confirmed"] = True
            result["error"] = safe_campaign_error(result)
        elif chunks_ok and not finalize_ok:
            result["error"] = (
                "sessão não finalizada; recibo preservado para revisão."
            )
    except (_services.AuthError, _services.UploadError) as exc:
        result["error"] = safe_campaign_error({"error": str(exc)})
        phase = getattr(exc, "phase", None)
        result.update(safe_account_diagnostics({"phase": phase}))
        issue_code = getattr(exc, "account_issue_code", None)
        if not isinstance(issue_code, str):
            issue_code = None
        blocked_reason = getattr(exc, "blocked_reason", None)
        if isinstance(blocked_reason, str):
            blocked_reason = blocked_reason.casefold()
        # Keep protocol evidence, not arbitrary response headers or credentials.
        if blocked_reason in {"app_check", "user", "device", "uber-device"}:
            result["blocked_reason"] = blocked_reason
        appcheck_cause = getattr(exc, "appcheck_cause", None)
        if isinstance(appcheck_cause, str) and appcheck_cause:
            result["appcheck_cause"] = appcheck_cause
        status = (exc.status_code if isinstance(exc, _services.UploadError)
                  else exc.http_status)
        if type(status) is int and (status == -1 or 100 <= status <= 599):
            result["http_status"] = status
        # Match execution/runner's diagnostic contract, without carrying raw
        # Retry-After headers, response bodies or provider credentials. These
        # facts describe the failure; they do not schedule another attempt.
        for field in ("retry_after", "retry_after_seconds"):
            value = getattr(exc, field, None)
            if type(value) in (int, float) and 0 <= value <= 86400 and math.isfinite(value):
                result[field] = value
        if issue_code in {"authentication", "app_check", "restricted", "forbidden",
                          "network", "timeout", "service", "rate_limit", "identity",
                          "device", "uber_device", "access_paused", "policy", "version",
                           "dns", "tls", "invalid_response", "organization", "missing_access", "unknown"}:
            result["account_issue_code"] = issue_code
        if getattr(exc, "request_not_sent", False) is True:
            result["request_not_sent"] = True
        # Missing integrity credentials require attention, even if their
        # acquisition error was marked transient. Replaying this account would
        # repeat all media checks without evidence that access has recovered.
        result["authentication_required"] = (
            issue_code in {"authentication", "app_check"}
            or blocked_reason == "app_check"
            or (isinstance(exc, _services.UploadError) and status == 401
                and not blocked_reason))
        result["review_required"] = bool(
            getattr(exc, "review_required", False)
            or getattr(exc, "remote_effect_possible", False))
        if isinstance(exc, _services.AuthError):
            result["access_error"] = True
        result["restriction_confirmed"] = issue_code == "restricted"
        result["retryable"] = (not result["review_required"]
                               and not result["authentication_required"]
                               and (exc.retryable if isinstance(exc, _services.UploadError) else
                                    issue_code in {"network", "timeout", "service", "rate_limit"}))
    return sanitize_account_error(result)

REQUIRES = ('AuthError', 'MAX_DUR_MS', 'MIN_DUR_MS', 'Path', 'Session', 'UploadError', '_AccountRouteError', '_bind_session_email', '_build_sidecar', '_chunk_plan', '_chunk_video_path', '_cut_video_chunk', '_measured_video_pts', '_new_identity', '_nymeria_resample_delivery', '_per_account_video', '_pump_account_pending', '_reconcile_uploads', '_slice_imu_csv', '_terminal_retry_parent', 'account_registration_route', 'build_frames_csv_from_video', 'build_imu_csv', 'config', 'device_profile', 'ego4d', 'list_sidecars', 'nymeria', 'org_policy', 'probe_video', 'time', 'upload_session')
