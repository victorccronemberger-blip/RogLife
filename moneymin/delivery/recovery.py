"""Recovery upload protocol core with declared collaborators."""
from __future__ import annotations
from .acceptance import failure_diagnostics
from dataclasses import dataclass
from typing import Any

@dataclass
class RecoveryServices:
    MAX_CRASH_RESUMES: Any
    Path: Any
    STATE_COMPLETING: Any
    STATE_DONE: Any
    STATE_FAILED: Any
    STATE_LOSS: Any
    STATE_QUARANTINE: Any
    STATE_RETRY_LATE: Any
    TRANSIENT_STATES: Any
    UploadError: Any
    _existing_receipt_error: Any
    _finalize_session: Any
    _iso_now: Any
    _journal_creation_uncertain: Any
    _journal_flag: Any
    _journal_has_remote_receipt: Any
    _journal_recorded_at: Any
    _pending_recovery_stage: Any
    _remove_sidecar_archive: Any
    _session_resource_identity: Any
    _sidecar_filename: Any
    _sidecar_resume_payload: Any
    _uncertain_creation_error: Any
    _upload_single_chunk: Any
    _video_resume_payload: Any
    _with_retry: Any
    complete_upload: Any
    config: Any
    evaluate_upload: Any
    get_upload: Any
    is_pending_evaluation: Any
    is_pending_finalization: Any
    is_pending_transport: Any
    is_perfect: Any
    is_remote_recovery_candidate: Any
    is_terminal_remote_failure: Any
    journal_delivery_confirmed: Any
    journal_flags_valid: Any
    list_sidecars: Any
    load_sidecar: Any
    mark_remote_terminal_failure: Any
    save_sidecar: Any
    upload_session: Any
    validate_remote_recovery_receipt: Any

REQUIRES = ('MAX_CRASH_RESUMES', 'Path', 'STATE_COMPLETING', 'STATE_DONE', 'STATE_FAILED', 'STATE_LOSS', 'STATE_QUARANTINE', 'STATE_RETRY_LATE', 'TRANSIENT_STATES', 'UploadError', '_existing_receipt_error', '_finalize_session', '_iso_now', '_journal_creation_uncertain', '_journal_flag', '_journal_has_remote_receipt', '_journal_recorded_at', '_pending_recovery_stage', '_remove_sidecar_archive', '_session_resource_identity', '_sidecar_filename', '_sidecar_resume_payload', '_uncertain_creation_error', '_upload_single_chunk', '_video_resume_payload', '_with_retry', 'complete_upload', 'config', 'evaluate_upload', 'get_upload', 'is_pending_evaluation', 'is_pending_finalization', 'is_pending_transport', 'is_perfect', 'is_remote_recovery_candidate', 'is_terminal_remote_failure', 'journal_delivery_confirmed', 'journal_flags_valid', 'list_sidecars', 'load_sidecar', 'mark_remote_terminal_failure', 'save_sidecar', 'upload_session', 'validate_remote_recovery_receipt')

def enqueue_upload(_services, 
    session: Any,
    video_path: str | Path,
    org_key: str,
    task_id: str | None = None,
    session_id: str | None = None,
    content_type: str = "video/mp4",
    **kwargs: Any,
) -> UploadResult:
    """Executa o upload e persiste o sidecar — o upload entra na "fila".

    Depois de enfileirar, `pump_pending()` pode ser chamado para tentar de novo
    os chunks que ficaram em retry_late/loss/failed (comportamento do app de
    retomar uploads pendentes ao reabrir / voltar online).
    """
    kwargs.setdefault("persist_sidecar", True)
    return _services.upload_session(
        session=session,
        video_path=video_path,
        org_key=org_key,
        task_id=task_id,
        session_id=session_id,
        content_type=content_type,
        **kwargs,
    )
def _pending_recovery_stage(_services, item: dict[str, Any]) -> str:
    """Validate a selected journal before effects; shared with recovery UI."""
    index = item.get("chunk_index", 0)
    resumes = item.get("crash_resumes", 0)
    attempts = item.get("attempts", 0)
    expected = item.get("expected_chunk_count", 1)
    if (not _services.journal_flags_valid(item) or type(index) is not int or index < 0
            or type(resumes) is not int or resumes < 0
            or type(attempts) is not int or attempts < 0
            or type(expected) is not int or expected < 1):
        raise _services.UploadError("Registro de retomada inválido; os arquivos foram preservados para revisão.",
                          transient=False, phase="recovery")
    _services._journal_flag(item, "register_first")
    _services._journal_flag(item, "suppress_per_chunk_catbear")
    receipt = _services._journal_has_remote_receipt(item)
    if _services._journal_creation_uncertain(item):
        raise _services._uncertain_creation_error()
    phase = item.get("phase", "")
    upload_id = item.get("upload_id")
    has_id = isinstance(upload_id, str) and bool(upload_id.strip())
    complete_phases = {"transport_done", "completing", "complete"}
    finalize_phases = {"awaiting_finalize", "finalize", "finalizing"}
    if _services.is_terminal_remote_failure(item):
        stage = "terminal_failed"
    elif _services.is_remote_recovery_candidate(item):
        stage = "remote_probe"
    elif has_id and phase in complete_phases:
        stage = "complete"
    elif has_id and item.get("finalize_requested") is True and (
            phase in finalize_phases or _services.is_pending_finalization(item)
            or _services.is_pending_evaluation(item)):
        stage = "finalize"
    elif has_id and item.get("transport_artifact") == "sidecar" and phase in {
            "registered", "sas", "sas_ready", "sas_reminted", "transport", "sidecar_preflight"}:
        if item.get("state") == _services.STATE_FAILED and not _services.is_pending_transport(item):
            raise _services._existing_receipt_error()
        _services._sidecar_resume_payload(item)
        stage = "sidecar"
    elif _services.is_pending_transport(item):
        stage = "video"
    else:
        if receipt or phase in complete_phases | finalize_phases:
            # A post-transport state without a receipt cannot safely become
            # another create/PUT. Unknown receipt phases need explicit review.
            raise _services._existing_receipt_error()
        _services._journal_recorded_at(item)
        stage = "upload"
    _services._sidecar_filename(item.get("session_id"), index)
    return stage
def pump_pending(_services, 
    session: Any,
    state: str | None = None,
    max_retries: int = 3,
    retry_backoff: float = 1.5,
    account_email: str | None = None,
    required_org_key: str | None = None,
    session_ids: set[str] | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Processa sidecars pendentes (retry-late / loss / failed) e tenta de novo.

    Mimica `pumpUploadsScreen` / "SeeUploads resume when you're back online" do
    app: os uploads que falharam de forma transiente ficam persistidos e são
    retomados numa execução posterior.

    Para cada sidecar pendente, chama upload_session com o MESMO session_id e
    chunk_index, preservando a identidade do envio. Se o blob já chegou, retoma
    somente complete/finalize, sem reenviar o vídeo. O vídeo local precisa existir
    para retomadas anteriores ao transporte. Quando account_email é informado,
    somente journals daquela conta são processados. Com required_org_key,
    pendências de outras organizações ficam preservadas sem reenvio.

    Recibos conhecidos usam as rotas diretas complete/finalize. Um recibo em
    fase anterior sem driver de retomada compatível é preservado e recusado
    antes de checkpoints; não é convertido em um envio novo. Nessas rotas
    diretas, um horário histórico ausente é conservado sem gerar outro clock.

    Retorna a lista de sidecars atualizados (estado final de cada tentativa).
    """
    on_progress = kwargs.get("on_progress")
    if on_progress is not None and not callable(on_progress):
        raise _services.UploadError("Callback de retomada inválido.", transient=False, phase="preflight")
    all_journals = _services.list_sidecars()
    if state is None:
        pending = [s for s in all_journals if (isinstance(s.get("state"), str) and s.get("state") in _services.TRANSIENT_STATES)
                   or s.get("state") == _services.STATE_LOSS or _services.is_pending_finalization(s)
                   or _services.is_pending_evaluation(s) or _services.is_pending_transport(s)
                   or _services.is_remote_recovery_candidate(s) or _services.is_terminal_remote_failure(s)]
    else:
        pending = [item for item in all_journals if item.get("state") == state]
    session_email = getattr(session, "email", None)
    if isinstance(session_email, str) and session_email.strip():
        if account_email is not None and account_email.strip().casefold() != session_email.strip().casefold():
            raise _services.UploadError("A conta da retomada difere da sessão autenticada.", transient=False)
        account_email = session_email
    if account_email is not None:
        wanted_email = account_email.strip().casefold()
        pending = [item for item in pending
                   if str(item.get("account_email") or "").strip().casefold()
                   == wanted_email]

    if required_org_key is not None:
        pending = [item for item in pending
                   if item.get("org_key") == required_org_key]
    if session_ids is not None:
        pending = [item for item in pending if item.get("session_id") in session_ids]

    # Validate all selected rows before checkpoints. Coercion can turn
    # bool/string counters into a different operation than the persisted one.
    selected_chunks: set[tuple[str, int]] = set()
    remote_probe_rows: list[dict[str, Any]] = []
    for item in pending:
        recovery_stage = _services._pending_recovery_stage(item)
        if recovery_stage in {"sidecar", "video", "remote_probe"} and account_email is None:
            raise _services.UploadError("A retomada do transporte requer uma conta autenticada identificada.",
                              transient=False, phase="recovery")
        index = item.get("chunk_index", 0)
        identity = (item["session_id"], index)
        if identity in selected_chunks:
            raise _services.UploadError("Registros de retomada duplicados; os arquivos foram preservados para revisão.",
                              transient=False, phase="recovery")
        selected_chunks.add(identity)
        if recovery_stage in {"sidecar", "video", "remote_probe"}:
            remote_probe_rows.append(item)

    # A native receipt can already be terminal remotely even if an older
    # desktop journal still says transport/complete. Query each exact receipt
    # before touching large video files or issuing SAS/PUT/complete/fail.
    remote_resource_key: str | None = None
    remote_results: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
    if remote_probe_rows:
        _session_email, remote_resource_key = _services._session_resource_identity(session)
        for item in remote_probe_rows:
            receipt = _services.get_upload(session, str(item.get("upload_id") or ""))
            status = _services.validate_remote_recovery_receipt(
                item, receipt, session, owner_resource_key=remote_resource_key)
            remote_results.append((item, status, receipt))

    for item, remote_status, receipt in remote_results:
        if remote_status == "failed":
            _services.mark_remote_terminal_failure(
                item, receipt, session, owner_resource_key=remote_resource_key)
            _services.save_sidecar(item)
        elif remote_status == "initiated":
            changed = item.get("state") == _services.STATE_FAILED
            if changed:
                item["state"] = _services.STATE_RETRY_LATE
            if item.get("phase") in {"complete", "completing"}:
                item.update(state=_services.STATE_RETRY_LATE, phase="transport",
                            transport_artifact="video")
                changed = True
            if item.get("transport_artifact") != "sidecar":
                _services._video_resume_payload(item, kwargs.get("profile"))
            if changed:
                _services.save_sidecar(item)
        elif remote_status == "uploaded":
            # `uploaded` proves the video PUT only. The original ZIP remains
            # mandatory for a native receipt; validate it before recording a
            # checkpoint or allowing the existing driver to continue.
            zip_bound_row = dict(item)
            zip_bound_row.update(transport_artifact="sidecar", conflict_action="complete")
            _services._sidecar_resume_payload(zip_bound_row)
            item["remote_recovery_receipt"] = dict(receipt)
            item.update(state=_services.STATE_RETRY_LATE, phase="sidecar_preflight",
                        transport_artifact="sidecar", conflict_action="complete")
            _services.save_sidecar(item)
        elif remote_status == "completed":
            item["remote_recovery_receipt"] = dict(receipt)
            item["raw_complete"] = {
                "id": item["upload_id"],
                "status": "completed",
                "meta": receipt["meta"],
            }
            item.update(state=_services.STATE_DONE, phase="done")
            _services.save_sidecar(item)

    # A sibling marked done/finalized can be absent from the pending filter.
    # Validate its receipt before spending another chunk's resume budget.
    selected_sessions = {item[0] for item in selected_chunks}
    for item in all_journals:
        if item.get("session_id") in selected_sessions:
            if item.get("finalized") is True:
                if not _services.journal_delivery_confirmed(item):
                    raise _services._existing_receipt_error()
            elif item.get("state") == _services.STATE_DONE:
                _services._pending_recovery_stage(item)

    updated: list[dict[str, Any]] = []
    touched_sessions: set[str] = set()
    for sidecar in pending:
        sid = str(sidecar.get("session_id") or "")
        idx = int(sidecar.get("chunk_index") or 0)
        touched_sessions.add(sid)
        if _services.is_terminal_remote_failure(sidecar):
            updated.append(sidecar)
            continue
        resumes = int(sidecar.get("crash_resumes") or 0)
        if resumes >= _services.MAX_CRASH_RESUMES:
            sidecar.update({
                "state": _services.STATE_QUARANTINE,
                "phase": "crash_resume_limit",
                "error": (
                    f"limite de {_services.MAX_CRASH_RESUMES} retomadas após reinício atingido"),
            })
            _services.save_sidecar(sidecar)
            updated.append(sidecar)
            continue
        recovery_stage = _services._pending_recovery_stage(sidecar)
        sidecar["crash_resumes"] = resumes + 1
        if recovery_stage == "finalize":
            sidecar.update(state=_services.STATE_COMPLETING, phase="awaiting_finalize",
                           **failure_diagnostics())
        _services.save_sidecar(sidecar)

        upload_id = str(sidecar.get("upload_id") or "")
        phase = str(sidecar.get("phase") or "")
        try:
            if recovery_stage == "finalize":
                # A crash can leave every completed chunk in done before the
                # session-level checkpoint is written. Preserve its receipt
                # and continue only evaluation/finalize, without local media.
                if _services.is_pending_finalization(sidecar) or _services.is_pending_evaluation(sidecar):
                    sidecar.update(state=_services.STATE_COMPLETING, phase="awaiting_finalize")
                    _services.save_sidecar(sidecar)
                updated.append(sidecar)
                continue
            # Blob já chegou: não repete vídeo nem cria outro registro.
            if upload_id and phase in {
                    "transport_done", "completing", "complete"}:
                complete_progress_attempt = 0
                def _resume_complete(
                    current_upload_id: str = upload_id,
                    current_size: int = int(sidecar.get("size_bytes") or 0),
                    suppress: bool = _services._journal_flag(sidecar, "suppress_per_chunk_catbear"),
                ) -> dict[str, Any]:
                    nonlocal complete_progress_attempt
                    sidecar.update(failure_diagnostics())
                    _services.save_sidecar(sidecar)
                    complete_progress_attempt += 1
                    if on_progress:
                        on_progress("complete", _services.STATE_COMPLETING, complete_progress_attempt)
                    return _services.complete_upload(
                        session, current_upload_id, current_size,
                        suppress_per_chunk_catbear=suppress,
                        session_complete=False,
                        _native_response_schema=_services._journal_flag(sidecar, "native_response_schema"),
                    )

                complete_data, attempts = _services._with_retry(
                    _resume_complete,
                    max_retries=max_retries,
                    retry_backoff=retry_backoff,
                    label="resume complete",
                )
                sidecar.update({
                    "state": (_services.STATE_COMPLETING if sidecar.get("finalize_requested")
                              else _services.STATE_DONE),
                    "phase": ("awaiting_finalize"
                              if sidecar.get("finalize_requested") else "done"),
                    "attempts": attempts,
                    "error": None,
                    **failure_diagnostics(),
                    "raw_complete": complete_data,
                })
                _services.save_sidecar(sidecar)
                if not sidecar.get("finalize_requested"):
                    _services._remove_sidecar_archive(sid, idx)
                updated.append(sidecar)
                continue

            recovery_stage = _services._pending_recovery_stage(sidecar)
            if recovery_stage in {"sidecar", "video"}:
                def _save_zip_checkpoint(**updates: Any) -> None:
                    sidecar.update(updates)
                    sidecar["updated_at"] = _services._iso_now()
                    _services.save_sidecar(sidecar)

                resume_profile = kwargs.get("profile")
                if recovery_stage == "video":
                    resume_video, _resume_zip = _services._video_resume_payload(sidecar, resume_profile)
                else:
                    resume_video = _services.Path(str(sidecar.get("local_video_path") or ""))
                chunk = _services._upload_single_chunk(
                    session=session, video_path=resume_video,
                    org_key=sidecar["org_key"], session_id=sid, chunk_index=idx,
                    task_id=sidecar.get("task_id"), content_type="video/mp4",
                    timeout_blob=kwargs.get("timeout_blob", 300),
                    recorded_at=sidecar["recorded_at"], device_meta=None,
                    platform_meta=None, video_meta=None, network_meta=None,
                    max_retries=max_retries, retry_backoff=retry_backoff,
                    suppress_per_chunk_catbear=_services._journal_flag(sidecar, "suppress_per_chunk_catbear"),
                    register_first=True, sidecar=True,
                    fail_on_error=kwargs.get("fail_on_error", True),
                    checkpoint=_save_zip_checkpoint, _resume_sidecar_row=sidecar,
                    on_progress=on_progress, profile=resume_profile,
                    **({"_expected_video_sha256": sidecar["video_content_sha256"]}
                       if recovery_stage == "video" else {}),
                )
                if chunk.state == _services.STATE_DONE:
                    sidecar.update(state=_services.STATE_COMPLETING if sidecar.get("finalize_requested") else _services.STATE_DONE,
                                   phase="awaiting_finalize" if sidecar.get("finalize_requested") else "done",
                                   raw_complete=chunk.raw_complete, error=None)
                    _services.save_sidecar(sidecar)
                    if not sidecar.get("finalize_requested"):
                        _services._remove_sidecar_archive(sid, idx)
                elif chunk.error:
                    sidecar.update(state=chunk.state, error=chunk.error,
                                   **failure_diagnostics(chunk))
                    _services.save_sidecar(sidecar)
                updated.append(sidecar)
                continue

            filename = str(sidecar.get("filename") or f"{sid}_{idx}.mp4")
            local_name = str(sidecar.get("local_video_path") or filename)
            video = _services.Path(local_name)
            if not video.exists():
                alt = _services.config.VIDEOS_DIR / filename
                if alt.exists():
                    video = alt
                else:
                    sidecar.update({
                        "state": _services.STATE_LOSS,
                        "phase": "missing_local_file",
                        "error": "arquivo local ausente (loss record)",
                    })
                    _services.save_sidecar(sidecar)
                    updated.append(sidecar)
                    continue

            archive_name = str(sidecar.get("sidecar_data_path") or "")
            archive = _services.Path(archive_name) if archive_name else None
            sidecar_payload = archive.read_bytes() if archive and archive.exists() else None
            require_sidecar = bool(sidecar.get("sidecar_data_path"))
            if require_sidecar and sidecar_payload is None:
                raise _services.UploadError(
                    "arquivo .data.zip persistido desapareceu",
                    transient=False, phase="preflight")

            result = _services.upload_session(
                session=session,
                video_path=video,
                org_key=str(sidecar.get("org_key") or ""),
                task_id=sidecar.get("task_id"),
                session_id=sid,
                recorded_at=str(sidecar.get("recorded_at") or "") or None,
                content_type="video/mp4",
                finalize=False,
                normalize=False,
                register_first=_services._journal_flag(sidecar, "register_first"),
                suppress_per_chunk_catbear=_services._journal_flag(sidecar, "suppress_per_chunk_catbear"),
                max_retries=max_retries,
                retry_backoff=retry_backoff,
                persist_sidecar=True,
                sidecar=require_sidecar,
                sidecar_data=sidecar_payload,
                chunk_index_start=idx,
                evaluate=bool(sidecar.get("evaluation_required", bool(sidecar.get("campaign_context")))
                              or kwargs.get("evaluate")),
                **{k: v for k, v in kwargs.items() if k in (
                    "fail_on_error", "timeout_blob",
                    "device_meta", "platform_meta", "video_meta",
                    "network_meta", "profile",
                    "on_progress",
                )},
            )
            chunk = result.chunks[0]
            current = _services.load_sidecar(sid, idx) or sidecar
            if chunk.state == _services.STATE_DONE and current.get("finalize_requested"):
                current.update(state=_services.STATE_COMPLETING, phase="awaiting_finalize")
                _services.save_sidecar(current)
            updated.append(current)
        except _services.UploadError as exc:
            sidecar.update({
                "state": (_services.STATE_QUARANTINE if exc.review_required else
                          _services.STATE_RETRY_LATE if exc.retryable or exc.authentication_required
                          else _services.STATE_FAILED),
                "phase": "completion_review" if exc.review_required else exc.phase or phase or "resume",
                **failure_diagnostics(exc),
                "error": str(exc),
            })
            _services.save_sidecar(sidecar)
            updated.append(sidecar)

    # Finalize só quando todos os chunks da sessão já estiverem completos.
    for sid in touched_sessions:
        journals = [item for item in _services.list_sidecars()
                    if str(item.get("session_id") or "") == sid]
        if required_org_key is not None and any(
                item.get("org_key") != required_org_key for item in journals):
            continue
        if not journals or not any(item.get("finalize_requested") for item in journals):
            continue
        if any(not _services.journal_flags_valid(item) for item in journals):
            continue
        owners = {str(item.get("account_email") or "").strip().casefold()
                  for item in journals}
        organizations = {str(item.get("org_key") or "") for item in journals}
        if len(owners) != 1 or not all(owners) or len(organizations) != 1 or not all(organizations):
            continue
        if any(item.get("task_id") != journals[0].get("task_id")
               or item.get("campaign_context") != journals[0].get("campaign_context")
               for item in journals):
            continue
        if account_email is not None and owners != {account_email.strip().casefold()}:
            continue
        counts = [item.get("expected_chunk_count", 1) for item in journals]
        if any(type(count) is not int or count < 1 for count in counts) or len(set(counts)) != 1:
            continue
        expected = counts[0]
        ready = [item for item in journals if item.get("phase") in {
            "awaiting_finalize", "finalize", "finalizing", "done"}
            and item.get("state") in {_services.STATE_COMPLETING, _services.STATE_RETRY_LATE, _services.STATE_DONE}
            and isinstance(item.get("upload_id"), str) and item["upload_id"].strip()]
        indices = [item.get("chunk_index") for item in ready]
        if (expected < 1 or len(ready) != expected or len(journals) != expected
                or any(type(index) is not int for index in indices)
                or set(indices) != set(range(expected))):
            continue

        # Recovery must not bypass the quality gate after complete, or after
        # a crash between evaluation and session finalization. Legacy campaign
        # journals without the new flag also require evaluation conservatively.
        evaluation_blocked = False
        for item in ready:
            required = item.get("evaluation_required", bool(item.get("campaign_context")))
            if not required or item.get("evaluation_verified") is True:
                continue
            try:
                if on_progress:
                    on_progress("evaluate", _services.STATE_COMPLETING, 1)
                evaluation = _services.evaluate_upload(session, str(item["upload_id"]))
                if not _services.is_perfect(evaluation):
                    raise _services.UploadError("Avaliação reprovada; envio preservado para revisão.",
                                      transient=False, phase="evaluate")
            except _services.UploadError as exc:
                item.update(state=_services.STATE_QUARANTINE, phase="evaluation_review", finalized=False,
                            evaluation_required=True, evaluation_verified=False,
                            **failure_diagnostics(exc),
                            evaluation_http_status=exc.status_code, error=str(exc))
                evaluation_blocked = True
            else:
                item.update(evaluation_required=True, evaluation_verified=True, **failure_diagnostics())
            _services.save_sidecar(item)
        if evaluation_blocked:
            continue

        org_key = str(ready[0].get("org_key") or "")
        finalize_progress_attempt = 0

        def _resume_finalize(
            current_org: str = org_key,
            current_sid: str = sid,
            current_expected: int = expected,
        ) -> int:
            nonlocal finalize_progress_attempt
            finalize_progress_attempt += 1
            if on_progress:
                on_progress("finalize", _services.STATE_COMPLETING, finalize_progress_attempt)
            ok, status = _services._finalize_session(
                session, current_org, current_sid, current_expected)
            if not ok:
                raise _services.UploadError(
                    f"finalize da sessão falhou ({status})",
                    status_code=status,
                    transient=(status == -1 or status in (408, 429) or status >= 500),
                    phase="finalize",
                )
            return status

        try:
            _services._with_retry(
                _resume_finalize, max_retries=max_retries,
                retry_backoff=retry_backoff, label="resume finalize")
        except _services.UploadError as exc:
            for item in ready:
                item.update({
                    "state": (_services.STATE_QUARANTINE if exc.review_required else
                              _services.STATE_COMPLETING if exc.retryable or exc.authentication_required
                              else _services.STATE_FAILED),
                    "phase": "finalize", "finalized": False,
                    "error": str(exc),
                    **failure_diagnostics(exc),
                })
                _services.save_sidecar(item)
        else:
            for item in ready:
                item.update({
                    "state": _services.STATE_DONE, "phase": "done",
                    "finalized": True, "error": None,
                    **failure_diagnostics(),
                })
                _services.save_sidecar(item)
                _services._remove_sidecar_archive(
                    sid, int(item.get("chunk_index") or 0))

    return [_services.load_sidecar(str(item.get("session_id") or ""),
                         int(item.get("chunk_index") or 0)) or item
            for item in updated]
