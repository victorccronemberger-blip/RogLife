"""Session upload protocol core with declared collaborators."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass
class SessionServices:
    CaptureDescriptor: Any
    CaptureImportError: Any
    Path: Any
    STATE_COMPLETING: Any
    STATE_CREATING: Any
    STATE_DONE: Any
    STATE_FAILED: Any
    STATE_QUARANTINE: Any
    UploadError: Any
    UploadResult: Any
    _chunk_sidecar: Any
    _existing_receipt_error: Any
    _finalize_session: Any
    _iso_now: Any
    _journal_creation_uncertain: Any
    _journal_flag: Any
    _journal_has_remote_receipt: Any
    _journal_recorded_at: Any
    _normalize_recorded_at_sequence: Any
    _original_group_payloads: Any
    _probe_duration_ms: Any
    _recorded_at_sequence_from_base: Any
    _remove_sidecar_archive: Any
    _require_completion_flags: Any
    _sidecar_archive_path: Any
    _uncertain_creation_error: Any
    _upload_owner: Any
    _upload_single_chunk: Any
    _validate_journal_owner: Any
    _verify_video_content_digest: Any
    _with_retry: Any
    default_device_meta: Any
    default_network_meta: Any
    default_platform_meta: Any
    default_video_meta: Any
    delete_session: Any
    delete_upload: Any
    evaluate_upload: Any
    fail_upload: Any
    format_recorded_at: Any
    hashlib: Any
    is_perfect: Any
    journal_flags_valid: Any
    load_sidecar: Any
    normalize_video: Any
    probe_video: Any
    re: Any
    recorded_at_to_wall_ms: Any
    recording_start_epoch: Any
    save_bytes: Any
    save_sidecar: Any
    summarize_checks: Any
    upload_session: Any
    uuid: Any

REQUIRES = ('CaptureDescriptor', 'CaptureImportError', 'Path', 'STATE_COMPLETING', 'STATE_CREATING', 'STATE_DONE', 'STATE_FAILED', 'STATE_QUARANTINE', 'UploadError', 'UploadResult', '_chunk_sidecar', '_existing_receipt_error', '_finalize_session', '_iso_now', '_journal_creation_uncertain', '_journal_flag', '_journal_has_remote_receipt', '_journal_recorded_at', '_normalize_recorded_at_sequence', '_original_group_payloads', '_probe_duration_ms', '_recorded_at_sequence_from_base', '_remove_sidecar_archive', '_require_completion_flags', '_sidecar_archive_path', '_uncertain_creation_error', '_upload_owner', '_upload_single_chunk', '_validate_journal_owner', '_verify_video_content_digest', '_with_retry', 'default_device_meta', 'default_network_meta', 'default_platform_meta', 'default_video_meta', 'delete_session', 'delete_upload', 'evaluate_upload', 'fail_upload', 'format_recorded_at', 'hashlib', 'is_perfect', 'journal_flags_valid', 'load_sidecar', 'normalize_video', 'probe_video', 're', 'recorded_at_to_wall_ms', 'recording_start_epoch', 'save_bytes', 'save_sidecar', 'summarize_checks', 'upload_session', 'uuid')

def _original_group_payloads(_services, 
    captures: list[CaptureDescriptor] | tuple[CaptureDescriptor, ...],
    paths: list[Path], *, session: Any, owner: str, org_key: str, task_id: str | None,
    session_id: str | None, recorded_at: str | list[str] | None,
    supplied: bytes | list[bytes] | None,
) -> tuple[list[CaptureDescriptor], list[bytes], list[int], list[str]]:
    """Reinspect all parts before journaling/HTTP; never repair source data."""
    def refuse() -> None:
        raise _services.UploadError("A captura original não corresponde ao plano revisado; "
                          "arquivos preservados para revisão.", transient=False,
                          phase="preflight", review_required=True) from None

    if (not isinstance(captures, (list, tuple)) or not captures
            or len(captures) != len(paths) or not owner
            or not isinstance(org_key, str) or not org_key.strip()
            or not isinstance(task_id, str) or not task_id.strip()
            or not isinstance(recorded_at, list) or len(recorded_at) != len(paths)):
        refuse()
    if any(not isinstance(capture, _services.CaptureDescriptor) for capture in captures):
        refuse()
    # The campaign, API and direct uploader share the same pure group policy.
    # The import is deferred because that validator calls the ZIP validator in
    # this module; it performs no upload or persistent-state operation.
    from ..original_capture import (OriginalCaptureError, prepare_original_capture_plan,
                                   validate_original_capture_session_policy)
    try:
        fresh = prepare_original_capture_plan(
            [(capture.media.path, capture.sidecar.path) for capture in captures],
            account_email=owner, org_key=org_key, task_id=task_id,
            expected_chunk_count=len(paths), probe=_services.probe_video)
    except OriginalCaptureError:
        refuse()
    if fresh.session_id != session_id or list(fresh.recorded_at) != recorded_at:
        refuse()
    observed = list(fresh.captures)
    payloads: list[bytes] = []
    for i, (expected, actual, path) in enumerate(zip(captures, observed, paths, strict=True)):
        try:
            if actual != expected or path.absolute() != actual.media.path:
                refuse()
            meta = actual.metadata
            source_os = meta["platform"].get("os", meta["platform"].get("type"))
            with actual.sidecar.path.open("rb") as stream:
                payload = stream.read(actual.sidecar.bytes + 1)
            if (len(payload) != actual.sidecar.bytes
                    or _services.hashlib.sha256(payload).hexdigest() != actual.sidecar.sha256):
                refuse()
            declared = meta["durationMs"]
            from ..validate import summarize, validate_upload_meta
            post_meta = {**meta, "device": {"model": meta["device"]["model"]},
                         "platform": {"os": source_os},
                         "chunk_index": i, "size_bytes": actual.media.bytes}
            if summarize(validate_upload_meta(post_meta, log_id=f"{session_id}_{i}",
                                              duration_ms=declared))["counts"].get("fail", 0):
                refuse()
        except (_services.CaptureImportError, OSError, KeyError, TypeError, ValueError, _services.UploadError):
            refuse()
        payloads.append(payload)
    if supplied is not None:
        given = supplied if isinstance(supplied, list) else [supplied]
        if given != payloads:
            refuse()
    validate_original_capture_session_policy(fresh, session)
    return observed, payloads, list(fresh.durations_ms), list(fresh.recorded_at)
def _verify_video_content_digest(_services, path: Path, digest: Any) -> None:
    if type(digest) is not str or _services.re.fullmatch(r"[a-f0-9]{64}", digest) is None:
        raise _services.UploadError("Hash do conteúdo inválido; novo preparo necessário.",
                          transient=False, phase="preflight", review_required=True)
    from ..content_provenance import fingerprint
    try:
        if fingerprint(path)["sha256"] != digest:
            raise ValueError("changed")
    except (OSError, ValueError):
        raise _services.UploadError("O conteúdo local mudou; novo preparo necessário, arquivos preservados para revisão.",
                          transient=False, phase="preflight", review_required=True) from None
def upload_session(_services, 
    session: Any,
    video_path: str | Path | list[str | Path],
    org_key: str,
    task_id: str | None = None,
    session_id: str | None = None,
    recorded_at: str | list[str] | None = None,
    content_type: str = "video/mp4",
    timeout_blob: int = 300,
    finalize: bool = True,
    evaluate: bool = False,
    device_meta: dict[str, Any] | None = None,
    platform_meta: dict[str, Any] | None = None,
    video_meta: dict[str, Any] | None = None,
    network_meta: dict[str, Any] | None = None,
    max_retries: int = 3,
    retry_backoff: float = 1.5,
    suppress_per_chunk_catbear: bool = True,
    fail_on_error: bool = True,
    persist_sidecar: bool = False,
    sidecar: bool = True,
    sidecar_data: bytes | list[bytes] | None = None,
    ego_meta: dict[str, Any] | None = None,
    require_perfect: bool = False,
    normalize: bool = True,
    register_first: bool = True,
    on_progress: Callable[..., None] | None = None,
    profile: DeviceProfile | None = None,
    chunk_index_start: int = 0,
    campaign_context: dict[str, Any] | None = None,
    original_captures: list[CaptureDescriptor] | tuple[CaptureDescriptor, ...] | None = None,
    expected_video_sha256: str | list[str] | None = None,
) -> UploadResult:
    """Executa o upload completo de uma sessão (1+ chunks) ao backend do Minute.

    Parâmetros:
        session       — `moneymin.minute_api.Session` autenticada.
        video_path    — caminho do vídeo (str/Path) ou lista de caminhos (multi-chunk).
        org_key       — resourceKey da organização destino.
        task_id       — UUID da task do Minute (opcional, liga o upload à task).
        session_id    — ID da sessão de gravação (UUID v4 gerado se não informado).
        content_type  — MIME type do vídeo (default video/mp4).
        timeout_blob  — timeout em segundos para o PUT no Azure (default 300).
        finalize      — se True, chama POST /sessions/{sid}/finalize ao final.
        evaluate      — se True, chama POST /uploads/{id}/evaluate em cada chunk.
        device_meta   — metadados de dispositivo (default: Pixel 8 Pro / Android 14).
        platform_meta — metadados de plataforma (default: android / app_version).
        video_meta    — metadados de vídeo (default: h264 / 1080p / 30fps).
        network_meta  — metadados de rede (default: wifi).
        max_retries   — limite de tentativas nas etapas compatíveis (default3).
                      CREATE sem recibo usa uma tentativa; falha mantém o
                      registro para revisão, sem presumir idempotência remota.
        retry_backoff — multiplicador do backoff entre retries (default 1.5).
        suppress_per_chunk_catbear — True em envios novos, como no helper do APK
                      1.29.0, independentemente da contagem de chunks. False
                      explícito mantém a omissão legada. Journal existente governa
                      a política efetiva: valor booleano salvo ou False se ausente.
        fail_on_error — se True, marca PATCH /fail quando o registro já existe
                      e a confirmação falha (comportamento _failUpload do app).
        persist_sidecar — se True, grava data/sidecars/<session_id>.json com o
                      estado de cada chunk (permite retomada via pump_pending).
        sidecar       — se True (default), gera e sobe o `.data.zip` nativo
                      (imu.csv/frames.csv/metadata.json) junto com o MP4 e envia
                      meta ego (source/timebase/cameras/codecActuals). Requisito
                      do evaluate do backend.
        require_perfect — política perfect-only: após o upload, roda o evaluate
                      e, se QUALQUER check falhar, marca PATCH /fail e DELETA o
                      upload + a sessão no backend (DELETE /uploads/{id} e
                      DELETE /sessions/{sid}). Só finaliza a sessão se perfeita.
        profile       — perfil de APARELHO da conta (device_profile). Com ele,
                      o sidecar usa calibração/uptime próprios e o meta usa o
                      modelo daquele aparelho (anti-colusão). Sem ele, usa o
                      Samsung padrão de referência (config.NATIVE_*).
        chunk_index_start — índice inicial do chunk; usado pela retomada para
                      manter a identidade original e evitar registros duplicados.
        on_progress   — callback síncrono(phase, state, attempt) no fluxo padrão, antes de
                      CREATE, SAS, PUT de vídeo/ZIP, COMPLETE, EVALUATE,
                      FINALIZE e compensação /fail, incluindo novas tentativas
                      dessas etapas. O chamador pode aguardar Retomar nele.
                      O progresso do transporte consulta o chamador antes de
                      cada bloco/tentativa e BlockList, inclusive no ZIP.
                      Requisições já em andamento podem terminar.
                      A política require_perfect tem compensações próprias.

    Devolve `UploadResult` com os IDs, metadados e status de finalize/evaluate.
    Levanta `UploadError` em qualquer falha.
    """
    _services._require_completion_flags(suppress_per_chunk_catbear)
    if type(register_first) is not bool:
        raise _services.UploadError("Política de ordem do upload inválida; use um valor booleano.",
                          transient=False, phase="preflight")
    if type(chunk_index_start) is not int or chunk_index_start < 0:
        raise _services.UploadError("Índice inicial do envio inválido.", transient=False, phase="preflight")
    session_owner = _services._upload_owner(session, profile)
    # Normaliza video_path para lista de Paths.
    if isinstance(video_path, (str, _services.Path)):
        paths = [_services.Path(video_path)]
    else:
        paths = [_services.Path(p) for p in video_path]
    expected_digests: list[str] | None = None
    if expected_video_sha256 is not None:
        if normalize is not False:
            raise _services.UploadError("Conteúdo byte-bound exige normalize=False.", transient=False, phase="preflight")
        expected_digests = ([expected_video_sha256] if type(expected_video_sha256) is str
                            else expected_video_sha256)
        if type(expected_digests) is not list or not paths or len(expected_digests) != len(paths):
            raise _services.UploadError("Grupo de hashes do conteúdo inválido.", transient=False, phase="preflight")
        # Validate the WHOLE group before any journal, CREATE or normalization.
        for path, digest in zip(paths, expected_digests, strict=True):
            _services._verify_video_content_digest(path, digest)

    original_durations: list[int] | None = None
    original_sequence: list[str] | None = None
    if original_captures is not None:
        if (normalize is not False or register_first is not True or sidecar is not True
                or persist_sidecar is not True or chunk_index_start != 0
                or any(type(value) is not bool for value in (evaluate, finalize, require_perfect, fail_on_error))
                or profile is not None or ego_meta is not None
                or any(value is not None for value in (device_meta, platform_meta, video_meta, network_meta))):
            raise _services.UploadError("A captura original exige envio integral sem alteração de arquivos ou metadados.",
                              transient=False, phase="preflight", review_required=True)
        original_captures, sidecar_data, original_durations, original_sequence = _services._original_group_payloads(
            original_captures, paths, session=session, owner=session_owner, org_key=org_key, task_id=task_id,
            session_id=session_id, recorded_at=recorded_at, supplied=sidecar_data)
        if expected_digests is not None and expected_digests != [capture.media.sha256 for capture in original_captures]:
            raise _services.UploadError("O hash informado difere da captura original.", transient=False, phase="preflight")

    explicit_session_id = session_id is not None
    session_id = session_id or str(_services.uuid.uuid4())
    existing_journals: list[dict[str, Any] | None] = []
    for offset in range(len(paths)):
        existing = (_services.load_sidecar(session_id, chunk_index_start + offset)
                    if persist_sidecar or explicit_session_id else None)
        if existing is not None:
            if existing.get("video_content_sha256") is not None:
                persisted_digest = existing["video_content_sha256"]
                if normalize is not False:
                    raise _services.UploadError("O registro byte-bound exige normalize=False; preserve os arquivos para revisão.",
                                      transient=False, phase="recovery", review_required=True)
                _services._verify_video_content_digest(paths[offset], persisted_digest)
                if expected_digests is not None and expected_digests[offset] != persisted_digest:
                    raise _services.UploadError("O hash do conteúdo difere do registro; preserve os recibos para revisão.",
                                      transient=False, phase="recovery", review_required=True)
            if original_captures is not None:
                # The original campaign core classifies/reconciles existing
                # receipts. Direct upload never upgrades an unowned legacy row
                # or replaces a queued attempt with a new CREATE.
                raise _services._existing_receipt_error()
            if not _services.journal_flags_valid(existing):
                raise _services.UploadError("Política do registro de envio inválida; preserve o arquivo para revisão.",
                                  transient=False, phase="recovery")
            if _services._journal_has_remote_receipt(existing):
                raise _services._existing_receipt_error()
            if _services._journal_creation_uncertain(existing):
                raise _services._uncertain_creation_error()
            _services._journal_recorded_at(existing)
            _services._journal_flag(existing, "suppress_per_chunk_catbear")
            _services._journal_flag(existing, "register_first")
            _services._validate_journal_owner(existing, session_owner)
            # Preserve the binding before normalizing media or replacing state.
            idx = chunk_index_start + offset
            expected_identity = {"org_key": org_key, "task_id": task_id,
                                 "log_id": f"{session_id}_{idx}",
                                 "filename": f"{session_id}_{idx}.mp4"}
            if any(existing.get(key) is not None and existing[key] != value
                   for key, value in expected_identity.items()):
                raise _services.UploadError("A identidade do envio difere do registro existente; preserve o arquivo para revisão.",
                                  transient=False, phase="recovery")
        existing_journals.append(existing)

    # A caller may add a quality gate, but cannot remove one already recorded
    # for a chunk. Legacy campaign journals follow the same policy as recovery.
    quality_required = [bool(
        evaluate or require_perfect or (row is not None and row.get(
            "evaluation_required", bool(row.get("campaign_context")))))
        for row in existing_journals]

    for p in paths:
        if not p.exists():
            raise _services.UploadError(f"vídeo não encontrado: {p}")

    preserved_sequence: list[str] | None = None
    if any(row is not None for row in existing_journals):
        original_durations = original_durations or [_services._probe_duration_ms(path) for path in paths]
        if recorded_at is None:
            if any(row is None for row in existing_journals):
                raise _services.UploadError("Uma sessão parcialmente registrada exige horários explícitos para os novos chunks; "
                                  "os horários existentes foram preservados.",
                                  transient=False, phase="recovery")
            preserved_sequence = [_services._journal_recorded_at(row) for row in existing_journals]
        else:
            if isinstance(recorded_at, list):
                candidate_sequence = _services._normalize_recorded_at_sequence(
                    [str(value) for value in recorded_at], original_durations)
            else:
                candidate_sequence = _services._recorded_at_sequence_from_base(
                    str(recorded_at), original_durations)
            preserved_sequence = list(candidate_sequence)
            for offset, row in enumerate(existing_journals):
                if row is None:
                    continue
                original_time = _services._journal_recorded_at(row)
                if _services.recorded_at_to_wall_ms(candidate_sequence[offset]) != _services.recorded_at_to_wall_ms(original_time):
                    raise _services.UploadError("O horário solicitado difere do horário original registrado; "
                                      "preserve o arquivo para revisão.", transient=False, phase="recovery")
                preserved_sequence[offset] = original_time
        # Validate bounds/order, but retain the exact original clock text.
        _services._normalize_recorded_at_sequence(preserved_sequence, original_durations)

    # Reencode full-range (yuvj420p) -> yuv420p antes de subir. Sem isso o
    # backend rejeita o preview (previewStatus "unavailable") e o vídeo não
    # abre no app. Com normalize=False o usuário assume o risco.
    if normalize:
        normalized: list[_services.Path] = []
        for p in paths:
            np = _services.normalize_video(p)
            if np != p:
                print(f"    [normalize] {p.name}: full-range yuvj420p -> yuv420p "
                      f"({np.name})")
            normalized.append(np)
        paths = normalized

    # Defaults de meta (mimica app nativo) se não fornecidos.
    if device_meta is None and original_captures is None:
        device_meta = _services.default_device_meta(profile)
    if platform_meta is None and original_captures is None:
        platform_meta = _services.default_platform_meta(profile)
    if video_meta is None and original_captures is None:
        video_meta = _services.default_video_meta()
    if network_meta is None and original_captures is None:
        network_meta = _services.default_network_meta()

    # recorded_at = início da gravação, formato JS toISOString (3 dígitos).
    # Sem valor: a gravação acabou de terminar (agora - duração), dentro do
    # backlog de 4h. Nunca o literal `.000Z` nem 6 dígitos.
    durations_ms = original_durations if original_captures is not None else [_services._probe_duration_ms(p) for p in paths]
    total_ms = sum(durations_ms)
    recorded_at_list: list[str] | None = None
    if original_sequence is not None:
        recorded_at_list = original_sequence
        recorded_at = original_sequence[0]
    elif preserved_sequence is not None:
        _services._normalize_recorded_at_sequence(preserved_sequence, durations_ms)
        recorded_at = preserved_sequence[0]
        recorded_at_list = preserved_sequence
    elif isinstance(recorded_at, list):
        recorded_at_list = _services._normalize_recorded_at_sequence(
            [str(value) for value in recorded_at], durations_ms)
        recorded_at = recorded_at_list[0] if recorded_at_list else ""
    if recorded_at_list is None:
        base_recorded_at = (
            str(recorded_at) if recorded_at else _services.format_recorded_at(
                _services.recording_start_epoch(total_ms / 1000.0))
        )
        sequence = _services._recorded_at_sequence_from_base(
            base_recorded_at, durations_ms)
        recorded_at = sequence[0]
        if len(sequence) > 1:
            recorded_at_list = sequence

    chunks: list[ChunkResult] = []
    total_size = 0
    total_duration = 0

    def _update_persisted_journals(**updates: Any) -> None:
        if not persist_sidecar:
            return
        for journal_index in range(
                int(chunk_index_start), int(chunk_index_start) + len(paths)):
            stored = _services.load_sidecar(session_id, journal_index)
            if not stored:
                continue
            stored.update(updates)
            stored["updated_at"] = _services._iso_now()
            _services.save_sidecar(stored)

    def _save_quality_checkpoint(chunk: ChunkResult, **updates: Any) -> None:
        if not persist_sidecar:
            return
        stored = _services.load_sidecar(session_id, chunk.chunk_index)
        if stored is None:
            raise _services.UploadError("Registro de qualidade ausente; preserve os recibos para revisão.",
                              transient=False, phase="evaluate", review_required=True)
        if updates.get("phase") == "evaluation_review" and "quality_review_origin_phase" not in stored:
            updates["quality_review_origin_phase"] = stored.get("phase", "")
        stored.update(updates)
        stored.update(evaluate_result=chunk.evaluate_result, updated_at=_services._iso_now())
        _services.save_sidecar(stored)

    for offset, vpath in enumerate(paths):
        idx = chunk_index_start + offset
        existing_row = existing_journals[offset]
        effective_suppression = (suppress_per_chunk_catbear if existing_row is None
                                 else _services._journal_flag(existing_row, "suppress_per_chunk_catbear"))
        effective_register_first = (register_first if existing_row is None
                                    else _services._journal_flag(existing_row, "register_first"))
        effective_native_schema = (register_first if existing_row is None
                                   else _services._journal_flag(existing_row, "native_response_schema"))
        chunk_recorded_at = (
            recorded_at_list[offset]
            if recorded_at_list and offset < len(recorded_at_list)
            else recorded_at
        )
        chunk_sidecar_data: bytes | None = None
        if isinstance(sidecar_data, list):
            if offset < len(sidecar_data):
                chunk_sidecar_data = sidecar_data[offset]
        else:
            chunk_sidecar_data = sidecar_data
        journal: dict[str, Any] | None = None
        checkpoint_callback: Callable[..., None] | None = None
        if persist_sidecar:
            existing = existing_row or {}
            journal = {
                **existing,
                "campaign_context": existing.get("campaign_context") or campaign_context,
                "schema_version": 2,
                "session_id": session_id,
                "org_key": org_key,
                "task_id": task_id,
                "chunk_index": idx,
                "log_id": f"{session_id}_{idx}",
                "filename": f"{session_id}_{idx}.mp4",
                "local_video_path": str(vpath.resolve()),
                "size_bytes": vpath.stat().st_size,
                "recorded_at": chunk_recorded_at,
                "state": _services.STATE_CREATING,
                "phase": "queued",
                "create_attempted": False,
                "attempts": int(existing.get("attempts") or 0),
                "crash_resumes": int(existing.get("crash_resumes") or 0),
                "finalize_requested": bool(
                    existing.get("finalize_requested", finalize)),
                "evaluation_required": quality_required[offset],
                "evaluation_verified": False,
                "expected_chunk_count": int(
                    existing.get("expected_chunk_count") or len(paths)),
                "updated_at": _services._iso_now(),
            }
            # An absent legacy flag stays absent; changing its spelling/policy
            # retroactively would change the next recovery operation.
            if existing_row is None or "register_first" in existing:
                journal["register_first"] = effective_register_first
            if existing_row is None or "suppress_per_chunk_catbear" in existing:
                journal["suppress_per_chunk_catbear"] = effective_suppression
            if existing_row is None or "native_response_schema" in existing:
                journal["native_response_schema"] = effective_native_schema
            if existing_row is None:
                journal["account_email"] = session_owner
            if expected_digests is not None:
                journal["video_content_sha256"] = expected_digests[offset]
            if original_captures is not None:
                original = original_captures[offset]
                journal.update(source_mode="original", original_media_sha256=original.media.sha256,
                               original_sidecar_sha256=original.sidecar.sha256,
                               physical_provenance_verified=False,
                               completion_strategy="explicit_finalize")
            if chunk_sidecar_data is not None:
                archive_path = _services._sidecar_archive_path(session_id, idx)
                _services.save_bytes(archive_path, chunk_sidecar_data)
                journal["sidecar_data_path"] = str(archive_path.resolve())
            _services.save_sidecar(journal)

            def _save_checkpoint(
                *, _journal: dict[str, Any] = journal,
                **updates: Any,
            ) -> None:
                _journal.update(updates)
                _journal["updated_at"] = _services._iso_now()
                _services.save_sidecar(_journal)

            checkpoint_callback = _save_checkpoint

        chunk = _services._upload_single_chunk(
            session=session,
            video_path=vpath,
            org_key=org_key,
            session_id=session_id,
            chunk_index=idx,
            task_id=task_id,
            content_type=content_type,
            timeout_blob=timeout_blob,
            recorded_at=chunk_recorded_at,
            device_meta=device_meta,
            platform_meta=platform_meta,
            video_meta=video_meta,
            network_meta=network_meta,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            suppress_per_chunk_catbear=effective_suppression,
            # Estratégia explícita de conclusão do QMoney. O sinal Android é
            # condicional a accepted && saveGated, sem equivalência comprovada
            # com estes estados desktop; não combinamos as duas estratégias.
            session_complete=False,
            fail_on_error=fail_on_error,
            sidecar=sidecar,
            sidecar_data=chunk_sidecar_data,
            ego_meta=ego_meta,
            register_first=effective_register_first,
            on_progress=on_progress,
            profile=profile,
            checkpoint=checkpoint_callback,
            _native_response_schema=effective_native_schema,
            **({"_original_capture": original_captures[offset]} if original_captures is not None else {}),
            **({"_expected_video_sha256": expected_digests[offset]} if expected_digests is not None else
               {"_expected_video_sha256": existing_row["video_content_sha256"]}
               if existing_row is not None and existing_row.get("video_content_sha256") is not None else {}),
        )
        chunks.append(chunk)
        total_size += chunk.size_bytes
        total_duration += chunk.duration_ms

        if persist_sidecar:
            final_journal = _services._chunk_sidecar(
                chunk, session_id, org_key, task_id, chunk_recorded_at,
                f"{chunk.log_id}.mp4", chunk.state, chunk.attempts, chunk.error,
            )
            if journal is not None:
                final_journal = {**journal, **final_journal}
            final_journal["local_video_path"] = str(vpath.resolve())
            final_journal["phase"] = (
                "done" if chunk.state == _services.STATE_DONE else final_journal.get("phase")
                or chunk.state)
            _services.save_sidecar(final_journal)

        if (chunk.state == _services.STATE_DONE and chunk.upload_id
                and not require_perfect and (evaluate or quality_required[offset])):
            try:
                if on_progress:
                    on_progress("evaluate", _services.STATE_COMPLETING, 1)
                chunk.evaluate_result = _services.evaluate_upload(session, chunk.upload_id)
            except _services.UploadError as exc:
                # Keep the original upload for review; an unavailable evaluation
                # must not be treated as a passed quality check.
                chunk.evaluate_result = {"error": str(exc), "http_status": exc.status_code}

        if original_captures is not None and chunk.state != _services.STATE_DONE:
            # Stop creating more parts after an unresolved original receipt.
            # Recovery must review the whole fixed SID, without a new identity.
            break

    result = _services.UploadResult(
        session_id=session_id,
        org_key=org_key,
        task_id=task_id,
        chunks=chunks,
        total_size_bytes=total_size,
        total_duration_ms=total_duration,
        recorded_at=recorded_at,
    )

    if any(quality_required) and not require_perfect:
        blocked = []
        for offset, chunk in enumerate(chunks):
            if (not quality_required[offset] or chunk.state != _services.STATE_DONE
                    or _services.is_perfect(chunk.evaluate_result)):
                continue
            summary = _services.summarize_checks(chunk.evaluate_result)
            failed = ", ".join(str(c.get("id") or "sem identificação")
                               for c in summary["failures"])
            http = (chunk.evaluate_result or {}).get("http_status")
            http_detail = f" (HTTP {http})" if type(http) is int else ""
            chunk.error = (f"Avaliação reprovada: {failed}." if summary["valid"] else
                           f"Avaliação inconclusiva{http_detail}; envio preservado para revisão.")
            chunk.state = _services.STATE_FAILED
            blocked.append(chunk)
        if blocked:
            retryable_evaluation = all(
                type((c.evaluate_result or {}).get("http_status")) is int
                and ((c.evaluate_result or {})["http_status"] in {-1, 408, 429}
                     or 500 <= (c.evaluate_result or {})["http_status"] <= 599)
                for c in blocked)
            _update_persisted_journals(
                state=_services.STATE_QUARANTINE, phase="evaluation_review", finalized=False,
                evaluation_required=True, evaluation_verified=False,
                evaluation_http_status=((blocked[0].evaluate_result or {})["http_status"]
                                        if retryable_evaluation else None),
                error="; ".join(dict.fromkeys(c.error for c in blocked)))
            return result

    # --- política perfect-only (se requisitada) ----------------------------
    # Evaluate the whole group once before cleanup. An inconclusive sibling
    # preserves the group for review. Valid rejection still uses the operator's
    # cleanup policy, with durable terminal journals before any remote write.
    if require_perfect:
        all_perfect = True
        evaluation_uncertain = False
        rejected: list[ChunkResult] = []
        for chunk in chunks:
            if not chunk.upload_id or chunk.state != _services.STATE_DONE:
                all_perfect = False
                evaluation_uncertain = True
                continue
            try:
                if on_progress:
                    on_progress("evaluate", _services.STATE_COMPLETING, 1)
                chunk.evaluate_result = _services.evaluate_upload(session, chunk.upload_id)
            except _services.UploadError as exc:
                chunk.evaluate_result = {"error": str(exc)}
                chunk.error = str(exc)
                chunk.state = _services.STATE_QUARANTINE
                evaluation_uncertain = True
                all_perfect = False
                continue
            if not _services.is_perfect(chunk.evaluate_result):
                summary = _services.summarize_checks(chunk.evaluate_result)
                failed = ", ".join(c.get("id", "?") for c in summary["failures"])
                print(f"    [perfect] chunk {chunk.chunk_index} REPROVADO: {failed}")
                chunk.state = _services.STATE_FAILED
                chunk.error = f"evaluate reprovado: {failed}"
                all_perfect = False
                rejected.append(chunk)
        if evaluation_uncertain:
            for chunk in chunks:
                _save_quality_checkpoint(
                    chunk, state=_services.STATE_QUARANTINE, phase="evaluation_review", finalized=False,
                    evaluation_required=True, evaluation_verified=False,
                    error="Avaliação inconclusiva; recibos da sessão preservados para revisão.")
            return result
        if rejected:
            # Commit the whole group's decision before compensation. A crash or
            # failed DELETE must never make recovery evaluate a deleted receipt.
            for chunk in chunks:
                _save_quality_checkpoint(
                    chunk, state=_services.STATE_QUARANTINE, phase="quality_rejected", finalized=False,
                    evaluation_required=True, evaluation_verified=False,
                    error=chunk.error or "Sessão reprovada na política de qualidade perfeita.",
                    remote_fail_attempted=False, remote_fail_confirmed=False,
                    upload_delete_attempted=False, upload_delete_confirmed=False,
                    session_delete_attempted=False, session_delete_confirmed=False)
            for chunk in rejected:
                for phase, attempted, confirmed, operation in (
                    ("fail", "remote_fail_attempted", "remote_fail_confirmed",
                     lambda current=chunk: _services.fail_upload(session, current.upload_id, current.error or "quality rejected")),
                    ("delete-upload", "upload_delete_attempted", "upload_delete_confirmed",
                     lambda current=chunk: _services.delete_upload(session, current.upload_id)),
                ):
                    if on_progress:
                        on_progress(phase, _services.STATE_FAILED, 1)
                    _save_quality_checkpoint(chunk, **{attempted: True})
                    try:
                        operation()
                    except _services.UploadError as exc:
                        _save_quality_checkpoint(chunk, **{confirmed: False, phase.replace("-", "_") + "_status": exc.status_code})
                    else:
                        _save_quality_checkpoint(chunk, **{confirmed: True, phase.replace("-", "_") + "_status": 200 if phase == "fail" else 204})
            if on_progress:
                on_progress("delete-session", _services.STATE_FAILED, 1)
            for chunk in chunks:
                _save_quality_checkpoint(chunk, session_delete_attempted=True)
            try:
                _services.delete_session(session, org_key, session_id)
            except _services.UploadError as exc:
                for chunk in chunks:
                    _save_quality_checkpoint(chunk, session_delete_confirmed=False, session_delete_status=exc.status_code)
            else:
                for chunk in chunks:
                    _save_quality_checkpoint(chunk, session_delete_confirmed=True, session_delete_status=204)
            return result
        if all_perfect:
            print(f"    [perfect] todos os {len(chunks)} chunk(s) passaram no evaluate")
        else:
            # não finaliza sessão reprovada
            return result

    if persist_sidecar and any(quality_required):
        for offset, chunk in enumerate(chunks):
            if (not quality_required[offset] or chunk.state != _services.STATE_DONE
                    or not _services.is_perfect(chunk.evaluate_result)):
                continue
            stored = _services.load_sidecar(session_id, chunk.chunk_index)
            if stored:
                stored.update(evaluation_required=True, evaluation_verified=True)
                _services.save_sidecar(stored)

    # --- 5. Finalize sessão ------------------------------------------------
    # O app só finaliza quando todos os chunks chegaram (expected_chunk_count).
    if finalize and all(c.state == _services.STATE_DONE for c in chunks):
        _update_persisted_journals(
            state=_services.STATE_COMPLETING, phase="finalizing", finalized=False)
        finalize_progress_attempt = 0

        def _finalize() -> int:
            nonlocal finalize_progress_attempt
            finalize_progress_attempt += 1
            if on_progress:
                on_progress("finalize", _services.STATE_COMPLETING, finalize_progress_attempt)
            ok, status = _services._finalize_session(
                session, org_key, session_id,
                expected_chunk_count=len(chunks),
            )
            result.finalize_status = status
            if not ok:
                raise _services.UploadError(
                    f"finalize da sessão falhou ({status}); esperado HTTP 200 ou 204",
                    status_code=status,
                    transient=(status == -1 or status in (408, 429) or status >= 500),
                    phase="finalize",
                )
            return status

        try:
            _services._with_retry(_finalize, max_retries=max_retries,
                        retry_backoff=retry_backoff, label="finalize")
            result.finalized = True
            _update_persisted_journals(
                state=_services.STATE_DONE, phase="done", finalized=True, error=None)
        except _services.UploadError as exc:
            result.finalized = False
            _update_persisted_journals(
                state=(_services.STATE_COMPLETING if exc.retryable else _services.STATE_FAILED),
                phase="finalize", finalized=False, error=str(exc),
            )
            # O chunk chegou, mas a sessão não está entregue. Preserve a causa
            # para o orquestrador repetir a conta em vez de aceitar falso ok.
            if chunks:
                chunks[-1].error = str(exc)

    if (persist_sidecar and all(c.state == _services.STATE_DONE for c in chunks)
            and (not finalize or result.finalized)):
        for chunk in chunks:
            stored = _services.load_sidecar(session_id, chunk.chunk_index) or {}
            if stored.get("finalize_requested") and stored.get("finalized") is not True:
                continue
            _services._remove_sidecar_archive(session_id, chunk.chunk_index)

    return result
def upload_video(_services, 
    session: Any,
    video_path: str | Path,
    org_key: str,
    task_id: str | None = None,
    session_id: str | None = None,
    log_id: str | None = None,
    content_type: str = "video/mp4",
    chunk_index: int = 0,
    timeout_blob: int = 300,
) -> UploadResult:
    """Compatibilidade com a interface antiga de chunk único.

    Usa upload_session internamente. Se log_id for fornecido, extrai o
    session_id e chunk_index dele (formato: {sessionId}_{chunkIndex}).

    Devolve `UploadResult` (use .upload_id, .blob_path, .log_id para os
    valores do primeiro/único chunk).
    """
    # Se log_id fornecido, decompor em session_id + chunk_index.
    if log_id and not session_id:
        parts = log_id.rsplit("_", 1)
        if len(parts) == 2 and parts[0] and parts[1].isdigit():
            session_id = parts[0]
            chunk_index = int(parts[1])

    return _services.upload_session(
        session=session,
        video_path=video_path,
        org_key=org_key,
        task_id=task_id,
        session_id=session_id,
        content_type=content_type,
        timeout_blob=timeout_blob,
        finalize=True,
        evaluate=False,
        chunk_index_start=chunk_index,
    )
