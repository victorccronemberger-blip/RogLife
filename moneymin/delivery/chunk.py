"""Chunk upload protocol core with declared collaborators."""
from __future__ import annotations
from .acceptance import failure_diagnostics
from dataclasses import dataclass
from typing import Any

@dataclass
class ChunkServices:
    CaptureImportError: Any
    ChunkResult: Any
    JsonStateError: Any
    Path: Any
    STATE_COMPLETING: Any
    STATE_CREATING: Any
    STATE_DONE: Any
    STATE_FAILED: Any
    STATE_LOSS: Any
    STATE_QUARANTINE: Any
    STATE_RETRY_LATE: Any
    STATE_TRANSPORT: Any
    UploadError: Any
    _conflict_upload: Any
    _existing_receipt_error: Any
    _http_upload_error: Any
    _journal_flag: Any
    _probe_duration_ms: Any
    _put_blob: Any
    _put_blob_file: Any
    _require_completion_flags: Any
    _response_upload_id: Any
    _session_request: Any
    _sidecar_resume_payload: Any
    _status_from_exception: Any
    _upload_owner: Any
    _validate_journal_owner: Any
    _validate_native_upload_out: Any
    _validate_sidecar_zip: Any
    _validated_sas_urls: Any
    _verify_video_content_digest: Any
    _video_resume_payload: Any
    _with_retry: Any
    build_metadata_json: Any
    build_sidecar_zip: Any
    complete_upload: Any
    config: Any
    decode_json_state: Any
    default_device_meta: Any
    default_platform_meta: Any
    fail_upload: Any
    hashlib: Any
    inspect_original_capture: Any
    is_pending_transport: Any
    probe_video: Any
    re: Any
    recorded_at_to_wall_ms: Any

REQUIRES = ('CaptureImportError', 'ChunkResult', 'JsonStateError', 'Path', 'STATE_COMPLETING', 'STATE_CREATING', 'STATE_DONE', 'STATE_FAILED', 'STATE_LOSS', 'STATE_QUARANTINE', 'STATE_RETRY_LATE', 'STATE_TRANSPORT', 'UploadError', '_conflict_upload', '_existing_receipt_error', '_http_upload_error', '_journal_flag', '_probe_duration_ms', '_put_blob', '_put_blob_file', '_require_completion_flags', '_response_upload_id', '_session_request', '_sidecar_resume_payload', '_status_from_exception', '_upload_owner', '_validate_journal_owner', '_validate_native_upload_out', '_validate_sidecar_zip', '_validated_sas_urls', '_verify_video_content_digest', '_video_resume_payload', '_with_retry', 'build_metadata_json', 'build_sidecar_zip', 'complete_upload', 'config', 'decode_json_state', 'default_device_meta', 'default_platform_meta', 'fail_upload', 'hashlib', 'inspect_original_capture', 'is_pending_transport', 'probe_video', 're', 'recorded_at_to_wall_ms')

def _validate_create_binding(_services, data: dict[str, Any], body: dict[str, Any]) -> None:
    """Reject contradictory echoes without requiring more than native UploadOut."""
    # The native schema requires only id/status/meta. Missing echoes remain valid;
    # present identities cannot bind this recording to a different receipt.
    # Do not constrain server task resolution when the optional task was omitted.
    for field in ("session_id", "log_id", "task_id"):
        if field in data and field in body and data[field] != body[field]:
            raise _services.UploadError(
                "O recibo CREATE diverge da gravação enviada; preserve o registro para revisão.",
                transient=False, phase="create", review_required=True,
            )

def _upload_single_chunk(_services, 
    session: Any,
    video_path: Path,
    org_key: str,
    session_id: str,
    chunk_index: int,
    task_id: str | None,
    content_type: str,
    timeout_blob: int,
    recorded_at: str,
    device_meta: dict[str, Any] | None,
    platform_meta: dict[str, Any] | None,
    video_meta: dict[str, Any] | None,
    network_meta: dict[str, Any] | None,
    max_retries: int = 3,
    retry_backoff: float = 1.5,
    suppress_per_chunk_catbear: bool = True,
    session_complete: bool = False,
    fail_on_error: bool = True,
    sidecar: bool = True,
    sidecar_data: bytes | None = None,
    ego_meta: dict[str, Any] | None = None,
    register_first: bool = True,
    on_progress: Callable[..., None] | None = None,
    profile: DeviceProfile | None = None,
    checkpoint: Callable[..., None] | None = None,
    _resume_sidecar_row: dict[str, Any] | None = None,
    _native_response_schema: bool = True,
    _original_capture: CaptureDescriptor | None = None,
    _expected_video_sha256: str | None = None,
) -> ChunkResult:
    """Executa registro -> SAS -> PUT Blob -> PATCH /complete.

    Novos envios usam a ordem observada em driveCreate do APK 1.29.0:
    POST /uploads -> SAS -> transporte -> complete. False é preservado para
    retomadas explicitamente identificadas com a ordem legada.

    Espelha o app nativo:
      - auto-retry com backoff nas etapas transientes (create/sas, transport);
      - PATCH complete com supressão True nos novos envios; False explícito
        mantém a omissão legada. `session_complete` só é enviado quando esse
        parâmetro é True; não há equivalência demonstrada entre o desktop e
        os estados Android accepted && saveGated (U04 permanece aberto);
      - se o registro foi criado e a confirmação falhar, marca `PATCH /fail`
        (fail_on_error) — comportamento `_failUpload` do app;
      - se o arquivo local sumir, devolve um ChunkResult em estado `loss`
        (loss record) em vez de abortar a sessão.
      - (sidecar=True) gera e sobe o `.data.zip` nativo junto com o MP4
        (imu.csv, frames.csv, metadata.json) e envia meta ego
        (source/timebase/cameras/codecActuals) — requisito do evaluate.

    Retorna o ChunkResult com os IDs e metadados. Política de ordem não booleana
    conserva o contrato de retorno STATE_FAILED e comunica somente o diagnóstico
    ao callback checkpoint, antes de probe/HTTP. Esse callback é código do
    chamador e pode produzir efeitos locais. Flags de conclusão não booleanas
    levantam UploadError antes de qualquer callback.
    """
    _services._require_completion_flags(suppress_per_chunk_catbear, session_complete)
    if _expected_video_sha256 is not None:
        _services._verify_video_content_digest(video_path, _expected_video_sha256)
    if type(_native_response_schema) is not bool:
        raise _services.UploadError("Contrato de resposta inválido; use um valor booleano.",
                          transient=False, phase="preflight")
    resume_row = _resume_sidecar_row
    resume_artifact: str | None = None
    if resume_row is not None:
        resume_artifact = ("sidecar" if resume_row.get("transport_artifact") == "sidecar"
                           else "video" if _services.is_pending_transport(resume_row) else None)
        if resume_artifact not in {"sidecar", "video"}:
            raise _services._existing_receipt_error()
        _native_response_schema = _services._journal_flag(resume_row, "native_response_schema")
        _services._validate_journal_owner(resume_row, _services._upload_owner(session, profile))
        if resume_artifact == "video" and resume_row.get("video_content_sha256") is not None:
            persisted_digest = resume_row["video_content_sha256"]
            if _expected_video_sha256 is not None and persisted_digest != _expected_video_sha256:
                raise _services.UploadError("O hash do conteúdo difere do registro; preserve os recibos para revisão.",
                                  transient=False, phase="preflight", review_required=True)
            _expected_video_sha256 = persisted_digest
            _services._verify_video_content_digest(video_path, _expected_video_sha256)
        if resume_artifact == "video":
            bound_video, sidecar_data = _services._video_resume_payload(resume_row, profile)
            try:
                if _services.Path(video_path).resolve(strict=True) != bound_video:
                    raise _services._existing_receipt_error()
            except (OSError, ValueError):
                raise _services._existing_receipt_error() from None
        else:
            sidecar_data = _services._sidecar_resume_payload(resume_row)
        if (resume_row.get("session_id") != session_id
                or resume_row.get("chunk_index", 0) != chunk_index
                or resume_row.get("org_key") != org_key
                or resume_row.get("task_id") != task_id
                or resume_row.get("recorded_at") != recorded_at
                or register_first is not True or sidecar is not True):
            raise _services._existing_receipt_error()
    if type(register_first) is not bool:
        error = "Política de ordem do upload inválida; use um valor booleano."
        if checkpoint:
            checkpoint(state=_services.STATE_FAILED, phase="preflight", error=error)
        return _services.ChunkResult(upload_id="", chunk_index=chunk_index,
                           log_id=f"{session_id}_{chunk_index}", blob_path="",
                           size_bytes=-1, duration_ms=0, state=_services.STATE_FAILED, error=error)
    _limits = _services.config.recording_limits()
    file_size = (resume_row["size_bytes"] if resume_row is not None
                 else video_path.stat().st_size if video_path.exists() else -1)
    # Padrão nativo: logId = "{sessionId}_{chunk_index}".
    log_id = f"{session_id}_{chunk_index}"
    # Padrão nativo observado no SAS real: filename = "{logId}.mp4" (SEM _preview).
    # Com esse nome o servidor gera blob_path = .../{logId}/{logId}.mp4, que é
    # exatamente o caminho que o evaluate procura (video.ffprobe_ok).
    file_name = f"{log_id}.mp4"
    sidecar_file_name = f"{log_id}.data.zip"
    duration_ms = (resume_row["duration_ms"] if resume_row is not None
                   else _original_capture.metadata["durationMs"] if _original_capture is not None
                   else _services._probe_duration_ms(video_path) if video_path.exists() else 0)

    def _emit(phase: str, state: str, attempt: int, **details: Any) -> None:
        if on_progress:
            on_progress(phase, state, attempt, **details)

    def _checkpoint(state: str, phase: str, **details: Any) -> None:
        if checkpoint:
            if state == _services.STATE_DONE:
                details.update(failure_diagnostics())
            checkpoint(state=state, phase=phase, **details)

    _checkpoint(
        _services.STATE_TRANSPORT if resume_row is not None else _services.STATE_CREATING,
        "sidecar_preflight" if resume_row is not None else "preflight",
        local_video_path=str(video_path.resolve()),
        size_bytes=file_size, duration_ms=duration_ms,
        **failure_diagnostics(),
        **({"transport_artifact": "video"} if resume_artifact == "video" else {}),
    )
    if resume_row is None and not video_path.exists():
        return _services.ChunkResult(
            upload_id="", chunk_index=chunk_index, log_id=log_id, blob_path="",
            size_bytes=-1, duration_ms=0, state=_services.STATE_LOSS,
            error="arquivo local ausente antes do preflight",
        )
    if (file_size <= 0
            or not _limits["min_duration_ms"] <= duration_ms <= _limits["max_duration_ms"]):
        return _services.ChunkResult(
            upload_id="", chunk_index=chunk_index, log_id=log_id, blob_path="",
            size_bytes=file_size, duration_ms=duration_ms, state=_services.STATE_FAILED,
            error=(
                "preflight recusou vídeo vazio ou fora da duração permitida "
                f"({duration_ms} ms; esperado {_limits['min_duration_ms']}–"
                f"{_limits['max_duration_ms']} ms)"),
        )

    # --- 0. sidecar .data.zip (nativo) ------------------------------------
    sidecar_bytes: bytes | None = None
    sidecar_metadata: dict[str, Any] | None = None
    if sidecar and (resume_row is not None or video_path.exists()):
        try:
            if sidecar_data is not None:
                sidecar_bytes = sidecar_data
            else:
                probe = _services.probe_video(video_path)
                # Sem device_meta/platform_meta: o metadata.json do sidecar usa
                # o device COMPLETO Android (Build.MODEL + systemName +
                # systemVersion; platform {os,version} no 1.29) — o que o catbear espera.
                # Com `profile` (anti-colusão): intrinsics Brown-Conrady e
                # elapsedRealtimeNanos DO APARELHO DA CONTA — cada upload tem
                # identidade de sensor própria.
                sidecar_kwargs: dict[str, Any] = {}
                wall_ms = _services.recorded_at_to_wall_ms(recorded_at)
                from ..mobile import physical_binding as pb
                from ..mobile.physical_binding import PhysicalDeviceRequiredError
                if pb.device_mode_requires_physical():
                    try:
                        bound = pb.require_campaign_physical_binding(wall_ms=wall_ms)
                    except PhysicalDeviceRequiredError as exc:
                        raise _services.UploadError(
                            str(exc), transient=False, phase="sidecar") from exc
                    # Full device signature from the connected phone, including
                    # Brown-Conrady calib and encoder GOP for that SSAID/model.
                    sidecar_kwargs = {
                        "uptime_ns": bound["uptime_ns"],
                        "device_meta": bound["sidecar_device"],
                        "platform_meta": bound["sidecar_platform"],
                        "app_version": bound["app_version"],
                        "calib": bound["calib"],
                        "frames_gop": bound["frames_gop"],
                        "imu_seed": bound["device_id"],
                    }
                elif profile is not None:
                    sidecar_kwargs = {
                        "calib": profile.calib,
                        "frames_gop": profile.frames_gop,
                        "uptime_ns": (profile.uptime_ns_at(wall_ms)
                                      if wall_ms else None),
                        # IMU própria por conta (sem ela todas sobem a MESMA IMU)
                        "imu_seed": profile.device_id,
                    }
                    # device COMPLETO do perfil (Build.MODEL Samsung da conta)
                    sidecar_kwargs["device_meta"] = profile.sidecar_device_meta()
                    sidecar_kwargs["platform_meta"] = profile.sidecar_platform_meta()
                sidecar_bytes = _services.build_sidecar_zip(
                    session_id=session_id,
                    chunk_index=chunk_index,
                    duration_ms=duration_ms,
                    recorded_at=recorded_at,
                    video_probe=probe,
                    # frames.csv derivado do MP4 real (PTS/keyframes) como o
                    # EgoSidecar lê com MediaExtractor — em QUALQUER upload.
                    video_path=video_path,
                    **sidecar_kwargs,
                )
            sidecar_metadata = _services._validate_sidecar_zip(
                sidecar_bytes, log_id=log_id, duration_ms=duration_ms)
        except Exception as exc:  # noqa: BLE001 — convertido em falha fechada
            error = exc if isinstance(exc, _services.UploadError) else _services.UploadError(
                f"falha ao criar sidecar obrigatório: {exc}", transient=False)
            _emit("sidecar", _services.STATE_FAILED, 1, error=str(error))
            _checkpoint(_services.STATE_FAILED, "sidecar", error=str(error))
            return _services.ChunkResult(
                upload_id=resume_row["upload_id"] if resume_row is not None else "",
                chunk_index=chunk_index, log_id=log_id,
                blob_path="", size_bytes=file_size, duration_ms=duration_ms,
                state=_services.STATE_FAILED, error=str(error),
            )
        _checkpoint(
            _services.STATE_TRANSPORT if resume_row is not None else _services.STATE_CREATING,
            "sidecar_preflight" if resume_row is not None else "sidecar_validated",
            sidecar_size_bytes=len(sidecar_bytes),
            sidecar_sha256=_services.hashlib.sha256(sidecar_bytes).hexdigest(),
        )

    upload_id = resume_row["upload_id"] if resume_row is not None else ""
    create_data: dict[str, Any] = {"id": upload_id} if resume_row is not None else {}
    create_attempts = 1
    conflict_action = "complete" if resume_row is not None else ""
    transport_sidecar_only = resume_artifact == "sidecar"
    transport_resume = resume_row is not None

    # --- 2. POST /api/v1/storage/sas/blobs --------------------------------
    sas_progress_attempt = 0

    def _sas() -> tuple[dict[str, Any], dict[str, str], str, str]:
        nonlocal sas_progress_attempt
        sas_progress_attempt += 1
        _emit("sas", _services.STATE_TRANSPORT, sas_progress_attempt)
        files: list[dict[str, Any]] = [] if transport_sidecar_only else [
            {"filename": file_name, "content_type": content_type},
        ]
        if sidecar_bytes is not None:
            files.append({"filename": sidecar_file_name, "content_type": "application/zip"})
        sas_body = {
            "session_id": session_id,
            "files": files,
            "organization_resource_key": org_key,
        }
        status, text, response_headers = _services._session_request(
            session, "POST", "/api/v1/storage/sas/blobs", sas_body)
        if status != 200:
            raise _services._http_upload_error("SAS", status, text, response_headers)
        try:
            sas_data = _services.decode_json_state(text)
        except _services.JsonStateError:
            raise _services.UploadError("Resposta SAS sem JSON válido.", status_code=status,
                              transient=False, phase="sas") from None
        urls_by_filename, paths_by_filename = _services._validated_sas_urls(
            sas_data, [item["filename"] for item in files],
            _native_response_schema=_native_response_schema and register_first)
        return sas_data, urls_by_filename, paths_by_filename.get(file_name, ""), \
            paths_by_filename.get(sidecar_file_name, "")

    sas_data: dict[str, Any] = {}
    urls_by_filename: dict[str, str] = {}
    blob_path = ""
    sidecar_blob_path = ""
    blob_url = ""
    sas_attempts = 1

    def _request_sas_stage() -> ChunkResult | None:
        nonlocal sas_data, urls_by_filename, blob_path, sidecar_blob_path
        nonlocal blob_url, sas_attempts
        try:
            sas_result, sas_attempts = _services._with_retry(
                _sas, max_retries=max_retries,
                retry_backoff=retry_backoff, label="SAS")
            sas_data, urls_by_filename, blob_path, sidecar_blob_path = sas_result
            blob_url = urls_by_filename.get(file_name, "")
        except _services.UploadError as exc:
            sas_attempts = exc.attempts or sas_attempts
            failure_state = (_services.STATE_QUARANTINE if exc.review_required else
                             _services.STATE_RETRY_LATE if exc.retryable or exc.authentication_required
                             else _services.STATE_FAILED)
            _checkpoint(
                failure_state, "sas", upload_id=upload_id,
                error=str(exc), attempts=sas_attempts,
                **failure_diagnostics(exc),
            )
            # No fluxo nativo o registro já existe. Falha transiente preserva o
            # ID para a retomada reutilizar o mesmo registro; somente uma falha
            # permanente pode encerrá-lo no servidor.
            if (upload_id and fail_on_error and not exc.retryable
                    and not exc.authentication_required and not exc.review_required):
                try:
                    _emit("fail", _services.STATE_FAILED, 1)
                    _services.fail_upload(session, upload_id, f"SAS falhou: {exc}")
                except _services.UploadError:
                    pass
            return _services.ChunkResult(
                upload_id=str(upload_id), chunk_index=chunk_index, log_id=log_id,
                blob_path=blob_path, size_bytes=file_size,
                duration_ms=duration_ms, raw_create=create_data,
                state=failure_state, attempts=sas_attempts, error=str(exc),
                **failure_diagnostics(exc),
                sidecar_blob_path=sidecar_blob_path,
                sidecar_size_bytes=len(sidecar_bytes) if sidecar_bytes else 0,
            )
        _checkpoint(
            _services.STATE_TRANSPORT, "sas_ready", upload_id=upload_id,
            blob_path=blob_path, sidecar_blob_path=sidecar_blob_path,
            attempts=sas_attempts,
        )
        return None

    # Compatibilidade do modo legado. Campanhas usam register_first=True e
    # solicitam a SAS somente depois de o registro ser criado/reconciliado.
    if not register_first:
        sas_failure = _request_sas_stage()
        if sas_failure is not None:
            return sas_failure

    # --- 3. PUT no Azure Blob Storage (mp4 + sidecar) ---------------------
    if resume_row is None and not video_path.exists():
        # [upload] local file missing -> loss record
        loss = _services.ChunkResult(
            upload_id="", chunk_index=chunk_index, log_id=log_id,
            blob_path=blob_path, size_bytes=-1, duration_ms=0,
            state=_services.STATE_LOSS, attempts=1,
            error="arquivo local sumiu antes do transport (loss record)",
        )
        return loss

    if resume_row is None:
        file_size = video_path.stat().st_size
    transport_progress_attempt = 0

    def _transport() -> int:
        nonlocal transport_progress_attempt
        transport_progress_attempt += 1
        _emit("transport", _services.STATE_TRANSPORT, transport_progress_attempt)

        def _transport_progress(sent: int, total: int, elapsed: float,
                                *, artifact: str | None = None) -> None:
            speed_bps = sent / elapsed if elapsed > 0 else 0.0
            remaining = max(0, total - sent)
            eta_s = remaining / speed_bps if speed_bps > 0 else None
            _emit(
                "transport", _services.STATE_TRANSPORT, transport_progress_attempt,
                sent_bytes=sent, total_bytes=total,
                speed_bps=speed_bps, eta_s=eta_s,
                percent=(sent * 100.0 / total if total else 100.0),
                **({"artifact": artifact} if artifact else {}),
            )

        put_status = 201 if transport_sidecar_only else _services._put_blob_file(
            blob_url, video_path, content_type=content_type,
            timeout=timeout_blob, on_progress=_transport_progress,
            **({"expected_sha256": _original_capture.media.sha256}
               if _original_capture is not None else
               {"expected_sha256": _expected_video_sha256}
               if _expected_video_sha256 is not None else {}))
        if put_status != 201:
            raise _services._http_upload_error(
                "PUT Blob", int(put_status), "status inesperado")
        if sidecar_bytes is not None:
            zip_url = urls_by_filename.get(sidecar_file_name, "")
            if zip_url:
                _emit("transport", _services.STATE_TRANSPORT, transport_progress_attempt,
                      artifact="sidecar")
                # O nativeUploadTransport (AzureBlockUploader) sobe QUALQUER
                # arquivo em blocos (mp4 e .data.zip) via OkHttp.
                zip_status = _services._put_blob(zip_url, sidecar_bytes,
                                       content_type="application/zip",
                                       timeout=timeout_blob,
                                       resumable=None,
                                       on_progress=lambda sent, total, elapsed: _transport_progress(
                                           sent, total, elapsed, artifact="sidecar"))
                if zip_status != 201:
                    raise _services._http_upload_error(
                        "PUT sidecar", int(zip_status), "status inesperado")
        return put_status

    sas_remints = 0
    sas_remint_error: _services.UploadError | None = None

    def _transport_resilient() -> int:
        """Uma SAS expirada é renovada; outros 403 continuam permanentes."""
        nonlocal sas_data, urls_by_filename, blob_path, sidecar_blob_path
        nonlocal blob_url, sas_attempts, sas_remints, sas_remint_error
        # The old authorization was already refused. An exhausted renewal
        # cannot make those URLs usable; outer retries must retain its cause
        # rather than turn a transient SAS failure into permanent PUT /fail.
        if sas_remint_error is not None:
            raise sas_remint_error
        try:
            return _transport()
        except _services.UploadError as exc:
            if exc.status_code not in (401, 403) or sas_remints >= 1:
                raise
            sas_remints += 1
            try:
                renewed, remint_attempts = _services._with_retry(
                    _sas, max_retries=max_retries,
                    retry_backoff=retry_backoff, label="SAS remint")
            except _services.UploadError as renewal_error:
                sas_remint_error = renewal_error
                raise
            sas_data, urls_by_filename, blob_path, sidecar_blob_path = renewed
            blob_url = urls_by_filename.get(file_name, "")
            sas_attempts += remint_attempts
            _checkpoint(
                _services.STATE_TRANSPORT, "sas_reminted", blob_path=blob_path,
                sidecar_blob_path=sidecar_blob_path,
            )
            return _transport()

    # --- 1. POST /api/v1/uploads -------------------------------------------
    # O app nativo monta o meta como: {...metadata.json, chunk_index, size_bytes,
    # source, ...deviceUploadMeta}. Ele NÃO envia blob_path, network,
    # app_version (usa appVersion) nem sidecar_blob_path no meta — replicar ISSO
    # (espalhar o metadata.json inteiro) é o que o catbear espera.
    def _register() -> None:
        nonlocal upload_id, create_data, create_attempts, conflict_action, transport_sidecar_only
        # O app nativo espalha o metadata.json do sidecar como meta do POST
        # /uploads. Se um .data.zip foi fornecido (sidecar_bytes), extrair o
        # metadata.json dele (fonte de verdade: imu/frames/metadata REAIS) —
        # replica o comportamento que gerou sessões "Ótimo". Caso contrário,
        # monta com build_metadata_json.
        meta: dict[str, Any] | None = (
            dict(sidecar_metadata) if sidecar_metadata is not None else None)
        if meta is None:
            probe = _services.probe_video(video_path) if video_path.exists() else {}
            log_id_local = f"{session_id}_{chunk_index}"
            meta = _services.build_metadata_json(
                session_id=session_id,
                chunk_index=chunk_index,
                duration_ms=duration_ms,
                recorded_at=recorded_at,
                video_probe=probe,
                device_meta=device_meta,
                platform_meta=platform_meta,
                log_id=log_id_local,
            )
        meta["chunk_index"] = chunk_index
        meta["size_bytes"] = file_size
        # O app nativo espalha o metadata.json do sidecar e DEPOIS sobrescreve
        # device/platform/appVersion com getDeviceUploadMeta() — formato curto
        # Android (DETALHAMENTO §2.2):
        #   device   = {"model": "SM-S901E"}   (só Build.MODEL)
        #   platform = {"os": "android"}       (só os)
        #   appVersion = binaryAppVersion
        # O metadata.json DENTRO do sidecar permanece COMPLETO (model +
        # systemName/systemVersion; platform {os,version} no 1.29) — é o
        # sidecar que alimenta os checks; o POST usa o formato curto.
        # Com perfil: o MODELO SAMSUNG DO APARELHO DA CONTA (S21–S24).
        if _original_capture is not None:
            # Short POST fields come from the reviewed capture. The ZIP bytes
            # retain the full original device/platform and unknown metadata.
            meta["device"] = {"model": _original_capture.metadata["device"]["model"]}
            source_platform = _original_capture.metadata["platform"]
            meta["platform"] = {"os": source_platform.get("os", source_platform.get("type"))}
            meta["appVersion"] = _original_capture.metadata["appVersion"]
        else:
            from ..mobile import physical_binding as pb
            from ..mobile.physical_binding import PhysicalDeviceRequiredError
            if pb.device_mode_requires_physical():
                try:
                    bound = pb.require_campaign_physical_binding()
                except PhysicalDeviceRequiredError as exc:
                    raise _services.UploadError(
                        str(exc), transient=False, phase="create") from exc
                meta["device"] = bound["upload_device"]
                meta["platform"] = bound["upload_platform"]
                meta["appVersion"] = bound["app_version"]
            else:
                meta["device"] = _services.default_device_meta(profile)
                meta["platform"] = _services.default_platform_meta(profile)
                meta["appVersion"] = _services.config.APP_VERSION

        # QA local: o backend compara o meta do POST com o sidecar
        # (xcheck.metadata_json_matches_upload) — qualquer drift cai aqui.
        from ..validate import summarize as _summarize
        from ..validate import validate_upload_meta as _check_upload_meta
        _meta_check = _summarize(_check_upload_meta(
            meta, log_id=log_id, duration_ms=duration_ms))
        if _meta_check["counts"].get("fail", 0):
            details = "; ".join(f"{item['id']}: {item['detail']}"
                                for item in _meta_check["failures"])
            raise _services.UploadError(
                f"meta do POST /uploads reprovado no checklist local: {details}",
                transient=False,
            )

        upload_body: dict[str, Any] = {
            "session_id": session_id,
            "log_id": log_id,
            "duration_ms": duration_ms,
            "recorded_at": recorded_at,
            "meta": meta,
        }
        if task_id:
            upload_body["task_id"] = task_id
        create_progress_attempt = 0

        def _create_upload() -> dict[str, Any]:
            nonlocal create_progress_attempt, conflict_action, upload_id, create_data
            create_progress_attempt += 1
            _emit("create", _services.STATE_CREATING, create_progress_attempt)
            if _expected_video_sha256 is not None:
                _services._verify_video_content_digest(video_path, _expected_video_sha256)
            if _original_capture is not None:
                try:
                    current = _services.inspect_original_capture(_original_capture.media.path,
                                                       _original_capture.sidecar.path)
                    if current != _original_capture:
                        raise _services.CaptureImportError("source_changed")
                except (_services.CaptureImportError, OSError):
                    raise _services.UploadError("A captura original mudou antes do registro; arquivos preservados para revisão.",
                                      transient=False, phase="preflight", review_required=True) from None
            create_path = f"/api/v1/uploads?org_key={org_key}"
            # Local gates that never reach the network must run before
            # create_attempted. Network uncertainty still needs the marker.
            from ..minute_api import require_app_check_for_write
            require_app_check_for_write("POST", create_path)
            # Commit intent before the request: an abrupt process exit may
            # otherwise leave an accepted remote upload with no local ID.
            _checkpoint(_services.STATE_CREATING, "create_in_flight",
                        create_attempted=True, attempts=create_progress_attempt,
                        **failure_diagnostics())
            # O app nativo envia o org_key como QUERY PARAM (/uploads?org_key=...),
            # não no corpo — replicar isso é o que associa o upload ao catbear/org.
            try:
                status, text, response_headers = _services._session_request(
                    session, "POST", create_path, upload_body)
            except Exception as exc:
                # Auth failures retain their typed account diagnosis. Adapter
                # failures may follow an accepted request and must stay private.
                from ..minute_api import AuthError
                if isinstance(exc, AuthError):
                    # Local auth before Minute dispatch must not freeze the
                    # journal as an uncertain CREATE. Remote-effect AuthError
                    # (for example refresh after a sent 401) stays uncertain.
                    if getattr(exc, "remote_effect_possible", False):
                        raise
                    code = getattr(exc, "account_issue_code", None)
                    if code == "identity":
                        # The identity gate has a stable typed contract for its
                        # caller. This adapter reports a known local refusal, so
                        # retain the monotonic attempt marker, but persist proof
                        # that no request was sent before preserving the error.
                        _checkpoint(
                            _services.STATE_FAILED, "create",
                            attempts=create_progress_attempt,
                            account_issue_code=code,
                            authentication_required=True,
                            request_not_sent=True,
                            error="Identidade local incompatível antes do CREATE.",
                        )
                        raise
                    http_status = getattr(exc, "http_status", None)
                    blocked = "app_check" if code == "app_check" else None
                    if not isinstance(http_status, int):
                        http_status = (
                            401 if code in {
                                "authentication", "identity", "access_paused",
                                "forbidden", "restricted", "app_check",
                            } or code is None else None)
                    raise _services.UploadError(
                        str(exc) or "Autenticação local indisponível antes do CREATE.",
                        status_code=http_status,
                        transient=False,
                        phase="create",
                        blocked_reason=blocked,
                        request_not_sent=True,
                        review_required=False,
                    ) from None
                typed = isinstance(exc, _services.UploadError)
                # A confirmed local App Check refusal never submitted CREATE.
                if typed and exc.request_not_sent is True:
                    raise
                status_code = exc.status_code if typed else _services._status_from_exception(exc)
                blocked_reason = exc.blocked_reason if typed else None
                status_detail = f" ({status_code})" if status_code is not None else ""
                message = f"CREATE sem confirmação do serviço{status_detail}; preserve o registro para revisão."
                if blocked_reason:
                    reason = (blocked_reason if isinstance(blocked_reason, str) and _services.re.fullmatch(
                        r"[a-z][a-z0-9_-]{0,63}", blocked_reason) else "não especificado")
                    message += f" [bloqueio: {reason}]"
                raise _services.UploadError(
                    message, status_code=status_code,
                    transient=exc.transient if typed else True,
                    blocked_reason=blocked_reason,
                    phase="create",
                    review_required=exc.review_required if typed else False,
                    appcheck_cause=getattr(exc, "appcheck_cause", None) if typed else None,
                    appcheck_message=getattr(exc, "appcheck_message", None) if typed else None,
                ) from None
            if status == 409:
                data, conflict_action = _services._conflict_upload(text)
                return data
            # O schema diz response 201 (Created), não 200.
            if status not in (200, 201):
                raise _services._http_upload_error(
                    "POST /uploads", status, text, response_headers)
            try:
                data = _services.decode_json_state(text)
            except _services.JsonStateError:
                raise _services.UploadError("Resposta de registro do upload sem JSON válido.",
                                  status_code=status, transient=False, phase="create") from None
            # Keep a trustworthy ID even when the remaining acknowledgement
            # cannot authorize SAS/transport. Never replace it with a new CREATE.
            upload_id = _services._response_upload_id(data, status=status)
            create_data = data
            if _native_response_schema and register_first:
                _services._validate_native_upload_out(data, "create")
                _validate_create_binding(_services, data, upload_body)
            return data

        # session_id/log_id are stable request bindings, not a demonstrated
        # provider idempotency guarantee. A lost acknowledgement cannot safely
        # authorize another CREATE. Known409 receipts still follow their driver.
        create_data, create_attempts = _services._with_retry(
            _create_upload, max_retries=1, retry_backoff=retry_backoff,
            label="create",
        )
        # UploadOut usa "id" como campo do uploadId.
        upload_id = _services._response_upload_id(create_data)
        # conflict_action is set only by the validated409 parser, never by a
        # similarly named field in an ordinary server success response.
        dead_end = conflict_action == "dead-end"
        transport_sidecar_only = (register_first and conflict_action == "complete"
                                  and sidecar_bytes is not None)
        _checkpoint(
            _services.STATE_FAILED if dead_end else _services.STATE_TRANSPORT,
            "create_conflict_dead_end" if dead_end else "registered", upload_id=upload_id,
            conflict_action=conflict_action, attempts=create_attempts,
            **({"transport_artifact": "sidecar"} if transport_sidecar_only else {}),
        )
        if dead_end:
            raise _services.UploadError(
                "Registro conflitante em estado failed; envio preservado para revisão.",
                status_code=409, transient=False, phase="create_conflict_dead_end")

    # Ordem observada em driveCreate do APK 1.29.0: registro -> SAS -> transporte.
    if register_first:
        try:
            if resume_row is None:
                _register()
        except _services.UploadError as exc:
            create_attempts = exc.attempts or create_attempts
            failure_state = (_services.STATE_QUARANTINE if exc.review_required else
                             _services.STATE_RETRY_LATE if exc.retryable or
                             (exc.authentication_required and exc.request_not_sent is True)
                             else _services.STATE_FAILED)
            _checkpoint(failure_state,
                        "registration_review" if exc.review_required else
                        "create_conflict_dead_end" if conflict_action == "dead-end" else "create",
                        error=str(exc), upload_id=upload_id,
                        attempts=create_attempts,
                        **failure_diagnostics(exc))
            return _services.ChunkResult(
                upload_id=str(upload_id), chunk_index=chunk_index, log_id=log_id, blob_path=blob_path,
                size_bytes=file_size, duration_ms=duration_ms,
                raw_create=create_data, state=failure_state,
                attempts=create_attempts, error=str(exc),
                **failure_diagnostics(exc),
            )
        if conflict_action != "complete" or transport_sidecar_only or transport_resume:
            sas_failure = _request_sas_stage()
            if sas_failure is not None:
                return sas_failure

    put_attempts = 1
    if conflict_action != "complete" or transport_sidecar_only or transport_resume:
        try:
            _, put_attempts = _services._with_retry(
                _transport_resilient, max_retries=max_retries,
                retry_backoff=retry_backoff,
                label="transport",
            )
        except _services.UploadError as exc:
            put_attempts = exc.attempts or 1
            failure_state = (_services.STATE_QUARANTINE if exc.review_required else
                             _services.STATE_RETRY_LATE if exc.retryable or exc.authentication_required
                             else _services.STATE_FAILED)
            _checkpoint(
                failure_state, "transport_review" if exc.review_required else "transport", upload_id=upload_id,
                error=str(exc), attempts=put_attempts,
                **failure_diagnostics(exc),
                **({"transport_artifact": resume_artifact or "video"}
                   if transport_resume else {}),
            )
            # Só uma falha permanente deve encerrar o registro no servidor.
            if (register_first and upload_id and fail_on_error
                    and not exc.retryable and not exc.review_required and not exc.authentication_required):
                try:
                    _emit("fail", _services.STATE_FAILED, 1)
                    _services.fail_upload(session, upload_id, f"transport falhou: {exc}")
                except _services.UploadError:
                    pass
            return _services.ChunkResult(
                upload_id=str(upload_id), chunk_index=chunk_index, log_id=log_id,
                blob_path=blob_path, size_bytes=file_size, duration_ms=duration_ms,
                state=failure_state, attempts=put_attempts, error=str(exc),
                **failure_diagnostics(exc),
            )
        _checkpoint(
            _services.STATE_COMPLETING, "transport_done", upload_id=upload_id,
            attempts=put_attempts,
        )

    if not register_first:
        try:
            _register()
        except _services.UploadError as exc:
            create_attempts = exc.attempts or create_attempts
            failure_state = (_services.STATE_QUARANTINE if exc.review_required else
                             _services.STATE_RETRY_LATE if exc.retryable or
                             (exc.authentication_required and exc.request_not_sent is True)
                             else _services.STATE_FAILED)
            _checkpoint(failure_state,
                        "registration_review" if exc.review_required else
                        "create_conflict_dead_end" if conflict_action == "dead-end" else "create",
                        error=str(exc), upload_id=upload_id,
                        attempts=create_attempts,
                        **failure_diagnostics(exc))
            return _services.ChunkResult(
                upload_id=str(upload_id), chunk_index=chunk_index, log_id=log_id, blob_path=blob_path,
                size_bytes=file_size, duration_ms=duration_ms,
                raw_create=create_data, state=failure_state,
                attempts=create_attempts, error=str(exc),
                **failure_diagnostics(exc),
            )

    # --- 4. PATCH /api/v1/uploads/{id}/complete ----------------------------
    complete_progress_attempt = 0

    def _complete() -> dict[str, Any]:
        nonlocal complete_progress_attempt
        complete_progress_attempt += 1
        _emit("complete", _services.STATE_COMPLETING, complete_progress_attempt)
        # network_type is resolved inside complete_upload (device mode → phone).
        return _services.complete_upload(
            session, upload_id, file_size,
            suppress_per_chunk_catbear=suppress_per_chunk_catbear,
            session_complete=session_complete,
            _native_response_schema=_native_response_schema and register_first,
        )

    complete_attempts = 1
    _checkpoint(_services.STATE_COMPLETING, "completing", upload_id=upload_id)
    try:
        complete_data, complete_attempts = _services._with_retry(
            _complete, max_retries=max_retries, retry_backoff=retry_backoff,
            label="complete",
        )
    except _services.UploadError as exc:
        complete_attempts = exc.attempts or complete_attempts
        error_msg = f"complete falhou após {complete_attempts} tentativas: {exc}"
        # 5xx/rede preservam upload_id e estado para retomar só o complete.
        failure_state = (_services.STATE_QUARANTINE if exc.review_required else
                         _services.STATE_COMPLETING if exc.retryable or exc.authentication_required
                         else _services.STATE_FAILED)
        _checkpoint(
            failure_state, "completion_review" if exc.review_required else "complete", upload_id=upload_id,
            error=error_msg, attempts=complete_attempts,
            **failure_diagnostics(exc),
        )
        if fail_on_error and not exc.retryable and not exc.review_required and not exc.authentication_required:
            try:
                _emit("fail", _services.STATE_FAILED, 1)
                _services.fail_upload(session, upload_id, error_msg)
            except _services.UploadError:
                pass  # melhor esforço — o erro original é o que importa
        return _services.ChunkResult(
            upload_id=str(upload_id), chunk_index=chunk_index, log_id=log_id,
            blob_path=blob_path, size_bytes=file_size, duration_ms=duration_ms,
            raw_create=create_data, state=failure_state,
            attempts=complete_attempts, error=error_msg,
            **failure_diagnostics(exc),
        )

    _checkpoint(_services.STATE_DONE, "done", upload_id=upload_id)
    return _services.ChunkResult(
        upload_id=str(upload_id),
        chunk_index=chunk_index,
        log_id=log_id,
        blob_path=blob_path,
        size_bytes=file_size,
        duration_ms=duration_ms,
        raw_create=create_data,
        raw_complete=complete_data,
        state=_services.STATE_DONE,
        attempts=max(sas_attempts, put_attempts, create_attempts, complete_attempts),
        sidecar_blob_path=sidecar_blob_path,
        sidecar_size_bytes=len(sidecar_bytes) if sidecar_bytes else 0,
    )
