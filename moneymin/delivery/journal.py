"""Journal upload protocol core with declared collaborators."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

@dataclass
class JournalServices:
    JsonStateError: Any
    Path: Any
    STATE_FAILED: Any
    STATE_RETRY_LATE: Any
    STATE_TRANSPORT: Any
    UploadError: Any
    _CURL_ERROR_CODE: Any
    _CURL_TRANSPORT_RETRY_CODES: Any
    _csv_span_ns: Any
    _iso_now: Any
    _journal_flag: Any
    _journal_recorded_at: Any
    _load_sidecar_from_path: Any
    _read_sidecar_file: Any
    _response_upload_id: Any
    _sidecar_archive_path: Any
    _sidecar_filename: Any
    _sidecar_path: Any
    _sidecar_resume_payload: Any
    _validate_journal_owner: Any
    _validate_sidecar_zip: Any
    _verify_video_content_digest: Any
    decode_json_state: Any
    hashlib: Any
    io: Any
    is_pending_transport: Any
    journal_flags_valid: Any
    json: Any
    load_json_state: Any
    re: Any
    save_json: Any
    sidecars_dir: Any
    urllib: Any
    zipfile: Any

REQUIRES = ('JsonStateError', 'Path', 'STATE_FAILED', 'STATE_RETRY_LATE', 'STATE_TRANSPORT', 'UploadError', '_CURL_ERROR_CODE', '_CURL_TRANSPORT_RETRY_CODES', '_csv_span_ns', '_iso_now', '_journal_flag', '_journal_recorded_at', '_load_sidecar_from_path', '_read_sidecar_file', '_response_upload_id', '_sidecar_archive_path', '_sidecar_filename', '_sidecar_path', '_sidecar_resume_payload', '_validate_journal_owner', '_validate_sidecar_zip', '_verify_video_content_digest', 'decode_json_state', 'hashlib', 'io', 'is_pending_transport', 'journal_flags_valid', 'json', 'load_json_state', 're', 'save_json', 'sidecars_dir', 'urllib', 'zipfile')

def sidecars_dir(_services, ) -> Path:
    """Retomadas pertencem à instalação, não à biblioteca compartilhável."""
    from ..upload_storage import journal_directory
    return journal_directory()
def _sidecar_filename(_services, session_id: str, chunk_index: int = 0) -> str:
    if not isinstance(session_id, str) or not _services.re.fullmatch(r"[A-Za-z0-9_-]{1,160}", session_id) or type(chunk_index) is not int or chunk_index < 0:
        raise _services.UploadError("identificador de retomada inválido")
    suffix = "" if chunk_index == 0 else f"__{chunk_index}"
    return f"{session_id}{suffix}.json"
def _sidecar_path(_services, session_id: str, chunk_index: int = 0) -> Path:
    filename = _services._sidecar_filename(session_id, chunk_index)
    return _services.sidecars_dir() / filename
def _sidecar_archive_path(_services, session_id: str, chunk_index: int = 0) -> Path:
    return _services._sidecar_path(session_id, chunk_index).with_suffix(".data.zip")
def _remove_sidecar_archive(_services, session_id: str, chunk_index: int = 0) -> None:
    """Remove somente o ZIP temporário depois da entrega confirmada."""
    try:
        _services._sidecar_archive_path(session_id, chunk_index).unlink(missing_ok=True)
    except OSError:
        pass
def _sidecar_resume_payload(_services, item: dict[str, Any]) -> bytes:
    """Bind the original archive to a known uploaded receipt before effects.

    This is an integrity/identity check, not proof of physical sensor origin.
    Missing historical bindings require review rather than generating new data.
    """
    error = _services.UploadError("ZIP original ou vínculo de retomada inválido; preserve os arquivos para revisão.",
                        transient=False, phase="recovery")
    sid, index = item.get("session_id"), item.get("chunk_index", 0)
    _services._sidecar_filename(sid, index)
    _services._response_upload_id({"id": item.get("upload_id")})
    _services._journal_recorded_at(item)
    size, duration = item.get("size_bytes"), item.get("duration_ms")
    archive_size, archive_hash = item.get("sidecar_size_bytes"), item.get("sidecar_sha256")
    owner, org = item.get("account_email"), item.get("org_key")
    artifact = item.get("transport_artifact")
    sidecar_receipt = (artifact == "sidecar"
                       and item.get("conflict_action") == "complete")
    video_receipt = (artifact in (None, "video")
                     and item.get("phase") in {
                         "sidecar_preflight", "sas", "sas_ready", "sas_reminted", "transport"}
                     and item.get("create_attempted") is True)
    if (not (sidecar_receipt or video_receipt)
            or _services._journal_flag(item, "register_first") is not True
            or type(size) is not int or size <= 0
            or type(duration) is not int or duration <= 0
            or type(archive_size) is not int or archive_size <= 0
            or not isinstance(archive_hash, str) or not _services.re.fullmatch(r"[0-9a-f]{64}", archive_hash)
            or not isinstance(owner, str) or not owner.strip()
            or not isinstance(org, str) or not org.strip()
            or item.get("log_id") != f"{sid}_{index}"
            or item.get("filename") != f"{sid}_{index}.mp4"):
        raise error
    name = item.get("sidecar_data_path")
    expected = _services._sidecar_archive_path(sid, index).resolve()
    try:
        if not isinstance(name, str) or _services.Path(name).resolve() != expected:
            raise error
        payload = expected.read_bytes()
    except (OSError, ValueError, RuntimeError):
        raise error from None
    if len(payload) != archive_size or _services.hashlib.sha256(payload).hexdigest() != archive_hash:
        raise error
    _services._validate_sidecar_zip(payload, log_id=f"{sid}_{index}", duration_ms=duration)
    return payload
def is_pending_transport(_services, item: Any) -> bool:
    """Cheaply recognize a known-receipt video transport that can be resumed.

    File hashes and the exact archived ZIP are revalidated by the transport
    driver before its first checkpoint or network call. A legacy failed row is
    eligible only for known transient libcurl codes; arbitrary failed CREATE,
    SAS, and HTTP rows remain manual-review cases.
    """
    if not isinstance(item, dict):
        return False
    state = item.get("state")
    if state not in {_services.STATE_TRANSPORT, _services.STATE_RETRY_LATE, _services.STATE_FAILED}:
        return False
    phase = item.get("phase")
    artifact = item.get("transport_artifact")
    if artifact not in (None, "video", "sidecar"):
        return False
    sidecar_only = artifact == "sidecar"
    if state == _services.STATE_FAILED:
        # Only historical DNS/connection failures in the known PUT phase are
        # safe to revive. Failed SAS or other permanent stages need review.
        if phase != "transport":
            return False
        match = _services._CURL_ERROR_CODE.search(str(item.get("error") or ""))
        if not match or int(match.group(1)) not in _services._CURL_TRANSPORT_RETRY_CODES:
            return False
    elif phase not in {"sidecar_preflight", "sas", "sas_ready", "sas_reminted", "transport"}:
        return False
    elif phase != "transport" and artifact not in {"video", "sidecar"}:
        return False
    try:
        if (not _services.journal_flags_valid(item) or item.get("create_attempted") is not True
                or item.get("register_first") is not True
                or item.get("native_response_schema") is not True
                # Empty is the normal register-first receipt (no 409 conflict).
                or item.get("conflict_action") not in (None, "", "complete")
                or (sidecar_only and item.get("conflict_action") != "complete")):
            return False
        sid, index = item.get("session_id"), item.get("chunk_index", 0)
        _services._sidecar_filename(sid, index)
        _services._response_upload_id({"id": item.get("upload_id")})
        _services._journal_recorded_at(item)
        if (not isinstance(item.get("account_email"), str) or not item["account_email"].strip()
                or not isinstance(item.get("org_key"), str) or not item["org_key"].strip()
                or type(item.get("expected_chunk_count")) is not int
                or item["expected_chunk_count"] < 1
                or type(item.get("size_bytes")) is not int or item["size_bytes"] <= 0
                or type(item.get("duration_ms")) is not int or item["duration_ms"] <= 0
                or type(item.get("sidecar_size_bytes")) is not int
                or item["sidecar_size_bytes"] <= 0
                or not isinstance(item.get("sidecar_sha256"), str)
                or _services.re.fullmatch(r"[0-9a-f]{64}", item["sidecar_sha256"]) is None):
            return False
        if not sidecar_only and (
                not isinstance(item.get("video_content_sha256"), str)
                or _services.re.fullmatch(r"[0-9a-f]{64}", item["video_content_sha256"]) is None
                or not isinstance(item.get("local_video_path"), str)
                or not item["local_video_path"].strip()):
            return False
        if item.get("log_id") != f"{sid}_{index}" or item.get("filename") != f"{sid}_{index}.mp4":
            return False
    except (TypeError, ValueError, _services.UploadError):
        return False
    return True
def _video_resume_payload(_services, item: dict[str, Any], profile: DeviceProfile | None) -> tuple[Path, bytes]:
    """Validate the immutable MP4/ZIP/profile binding before resuming both PUTs."""
    error = _services.UploadError("MP4, perfil ou ZIP original inválido; preserve os arquivos para revisão.",
                        transient=False, phase="recovery", review_required=True)
    if item.get("transport_artifact") == "sidecar" or not _services.is_pending_transport(item):
        raise error
    try:
        video = _services.Path(item["local_video_path"])
        if video.is_symlink():
            raise error
        video = video.resolve(strict=True)
        if not video.is_file() or video.stat().st_size != item["size_bytes"]:
            raise error
        _services._verify_video_content_digest(video, item["video_content_sha256"])
        payload = _services._sidecar_resume_payload(item)
        metadata = _services._validate_sidecar_zip(
            payload, log_id=str(item["log_id"]), duration_ms=item["duration_ms"])
        session_meta = metadata.get("session")
        chunk_meta = metadata.get("chunk")
        if (metadata.get("createdAt") != _services._journal_recorded_at(item)
                or not isinstance(session_meta, dict)
                or session_meta.get("id") != item["session_id"]
                or not isinstance(chunk_meta, dict)
                or chunk_meta.get("index") != item["chunk_index"]):
            raise error
        owner = str(item["account_email"]).strip().casefold()
        if profile is None:
            from .. import device_profile
            profile = device_profile._load_profile(owner, migrate=False)
        if (profile is None or not isinstance(getattr(profile, "email", None), str)
                or profile.email.strip().casefold() != owner):
            raise error
        _services._validate_journal_owner(item, owner)
        device = metadata.get("device")
        platform = metadata.get("platform")
        video_meta = metadata.get("video")
        cameras = metadata.get("cameras")
        if (device != profile.sidecar_device_meta()
                or platform != profile.sidecar_platform_meta()
                or not isinstance(video_meta, dict)
                or type(video_meta.get("width")) is not int or video_meta["width"] <= 0
                or type(video_meta.get("height")) is not int or video_meta["height"] <= 0
                or not isinstance(cameras, list) or len(cameras) != 1
                or not isinstance(cameras[0], dict)):
            raise error
        from .. import sidecar as sidecar_module
        calibration = profile.calib
        camera = cameras[0]
        expected_intrinsics = sidecar_module._scale_camera_intrinsics(
            video_meta["width"], video_meta["height"], cal=calibration)
        if (camera.get("intrinsics") != expected_intrinsics
                or camera.get("name") != f"camera_logical_{calibration.get('logicalCameraId') or '3'}"
                or camera.get("rolling_shutter_readout_s")
                != float(calibration.get("readoutS") or 0.0105)):
            raise error
    except _services.UploadError:
        raise
    except (OSError, TypeError, ValueError, KeyError, AttributeError):
        raise error from None
    return video, payload
def save_sidecar(_services, sidecar: dict[str, Any]) -> Path:
    """Persiste o estado de um upload em `data/sidecars/<session_id>.json`."""
    if not isinstance(sidecar, dict):
        raise _services.UploadError("identificador de retomada inválido")
    candidate = dict(sidecar)
    sid = candidate.get("session_id", candidate.get("sessionId"))
    if "sessionId" in candidate and candidate["sessionId"] != sid:
        raise _services.UploadError("identificador de retomada inválido")
    # Missing indices belong to the legacy zero-chunk format. Present values
    # must retain their exact type: coercion can replace a different journal.
    chunk_index = candidate.get("chunk_index", 0)
    try:
        _services.json.dumps(candidate, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise _services.UploadError("O novo estado de envio não é um objeto JSON válido; o arquivo anterior foi preservado.",
                          transient=False, phase="recovery") from None
    path = _services._sidecar_path(sid, chunk_index)
    from ..operation_lease import operation_lease, OperationLeaseError
    from ..media_lifecycle import media_state_lease
    try:
        # Readers can scan thousands of journals under this shared barrier.
        # Publishing a checkpoint must wait for that coherent read, while
        # ownership of this specific journal remains nonblocking. Reuse the
        # already-resolved path so migration/account discovery does not run
        # again while the media barrier is held.
        with media_state_lease(wait=True, timeout_s=30.0), operation_lease(path.with_suffix('.write.lock')):
            previous = _services._load_sidecar_from_path(path, sid, chunk_index)
            if previous is not None and (any(
                    previous.get(key) is not None and previous.get(key) != candidate.get(key)
                    for key in ("account_email", "org_key", "task_id", "expected_chunk_count", "campaign_context"))
                    or (previous.get('create_attempted') is True and candidate.get('create_attempted') is False)
                    or (isinstance(previous.get('upload_id'),str) and previous['upload_id'].strip()
                        and previous['upload_id'] != candidate.get('upload_id'))):
                raise _services.UploadError("A identidade do registro de envio existente não corresponde ao novo estado; preserve o arquivo para revisão.",
                                  transient=False, phase="recovery")
            candidate["session_id"] = sid
            _services.save_json(path, candidate)
    except OperationLeaseError:
        raise _services.UploadError('O registro de envio já está em uso; preserve a retomada.',transient=True,phase='recovery') from None
    return path
def _read_sidecar_file(_services, path: Path) -> dict[str, Any] | None:
    """One authoritative decoder and filename contract for every journal reader."""
    data = _services.load_json_state(path, None)
    if data is None:
        return None
    if (not isinstance(data, dict)
            or _services._sidecar_filename(data.get("session_id"), data.get("chunk_index", 0)) != path.name):
        raise ValueError("invalid journal identity")
    return data
def _load_sidecar_from_path(_services, path: Path, session_id: str,
                            chunk_index: int) -> dict[str, Any] | None:
    """Decode one resolved journal without repeating storage migration."""
    try:
        data = _services._read_sidecar_file(path)
        if data is None:
            return None
        if (not isinstance(data, dict)
                or _services._sidecar_filename(data.get("session_id"), data.get("chunk_index", 0)) != path.name
                or data.get("session_id") != session_id or data.get("chunk_index", 0) != chunk_index):
            raise ValueError("invalid journal identity")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError, _services.UploadError):
        raise _services.UploadError("Não foi possível validar o registro de envio; preserve o arquivo para revisão.",
                          transient=False, phase="recovery") from None
    return data
def load_sidecar(_services, session_id: str, chunk_index: int = 0) -> dict[str, Any] | None:
    """Lê um recibo; somente a ausência real retorna None."""
    return _services._load_sidecar_from_path(_services._sidecar_path(session_id, chunk_index),
                                  session_id, chunk_index)
def list_sidecars(_services, state: str | None = None) -> list[dict[str, Any]]:
    """Valida todos os journals antes de retornar o filtro de estado."""
    from ..media_lifecycle import media_state_lease
    from ..operation_lease import OperationLeaseError
    out: list[dict[str, Any]] = []
    try:
        # Read one coherent generation while writers/cleanup hold the same
        # local barrier. Large journal stores and concurrent account workers
        # can serialize scans here; wait boundedly for a reader/writer to finish.
        # No network work occurs under this lease.
        with media_state_lease(wait=True, timeout_s=30.0):
            # iterdir reports directory I/O failures; glob can silently omit them.
            paths = sorted(path for path in _services.sidecars_dir().iterdir() if path.name.lower().endswith(".json"))
            for path in paths:
                data = _services._read_sidecar_file(path)
                if not isinstance(data, dict):
                    raise ValueError("invalid journal format")
                expected_name = _services._sidecar_filename(data.get("session_id"), data.get("chunk_index", 0))
                if path.name != expected_name:
                    raise ValueError("invalid journal identity")
                if state is None or data.get("state") == state:
                    out.append(data)
    except OperationLeaseError as exc:
        if exc.busy:
            raise _services.UploadError("Os registros de envio estão ocupados; tente novamente.",
                              transient=True, phase="recovery") from None
        raise _services.UploadError(
            "Não foi possível reservar a leitura dos registros; preserve-os para revisão.",
            transient=False, phase="recovery") from None
    except (OSError, UnicodeError, ValueError, _services.UploadError):
        # Corrupt or unknown receipts must never become proof of no prior upload.
        # Keep diagnostics independent of filenames, payloads and parser errors.
        raise _services.UploadError("Não foi possível validar todos os registros de envio; preserve os arquivos para revisão.",
                          transient=False, phase="recovery") from None
    return out
def _csv_span_ns(_services, 
    payload: bytes,
    timestamp_column: str,
    required_columns: tuple[str, ...],
) -> tuple[int, int, int]:
    """Valida um CSV temporal e devolve primeira/última marca e nº de amostras."""
    try:
        lines = payload.decode("utf-8-sig").splitlines()
    except UnicodeError as exc:
        raise _services.UploadError("sidecar contém CSV fora de UTF-8", transient=False) from exc
    if len(lines) < 3:
        raise _services.UploadError("sidecar contém CSV sem amostras suficientes", transient=False)
    header = [value.strip() for value in lines[0].split(",")]
    if len(set(header)) != len(header):
        raise _services.UploadError("sidecar CSV com colunas duplicadas", transient=False)
    missing = [column for column in required_columns if column not in header]
    if missing:
        raise _services.UploadError(
            "sidecar CSV sem coluna(s): " + ", ".join(missing),
            transient=False,
        )
    index = header.index(timestamp_column)
    try:
        stamps = []
        for line in lines[1:]:
            cells = line.split(",")
            if len(cells) != len(header) or not _services.re.fullmatch(r"[0-9]+", cells[index]):
                raise ValueError
            stamp = int(cells[index])
            if stamp > (1 << 63) - 1 or (stamps and stamp <= stamps[-1]):
                raise ValueError
            stamps.append(stamp)
        first, last = stamps[0], stamps[-1]
    except (IndexError, TypeError, ValueError) as exc:
        raise _services.UploadError("sidecar CSV com timestamp inválido", transient=False) from exc
    if last <= first:
        raise _services.UploadError("sidecar CSV sem relógio crescente", transient=False)
    return first, last, len(lines) - 1
def _validate_sidecar_zip(_services, 
    payload: bytes,
    *,
    log_id: str,
    duration_ms: int,
) -> dict[str, Any]:
    """Falha antes da rede se ZIP, metadata, IMU e frames forem incoerentes."""
    if not payload:
        raise _services.UploadError("sidecar obrigatório está vazio", transient=False)
    try:
        with _services.zipfile.ZipFile(_services.io.BytesIO(payload)) as archive:
            infos = archive.infolist()
            names = {info.filename for info in infos}
            required = {
                f"{log_id}.metadata.json",
                f"{log_id}.imu.csv",
                f"{log_id}.frames.csv",
            }
            if not required.issubset(names):
                missing = ", ".join(sorted(required - names))
                raise _services.UploadError(
                    f"sidecar incompleto; faltando: {missing}", transient=False)
            if len(names) != len(infos):
                raise _services.UploadError(
                    "sidecar contém membros duplicados", transient=False)
            if any("/" in name or "\\" in name or name.startswith(".")
                   for name in names):
                raise _services.UploadError(
                    "sidecar contém caminhos inesperados", transient=False)
            if sum(info.file_size for info in infos) > 512 * 1024 * 1024:
                raise _services.UploadError("sidecar descompactado excede 512 MiB", transient=False)
            metadata = _services.decode_json_state(archive.read(f"{log_id}.metadata.json"))
            imu = archive.read(f"{log_id}.imu.csv")
            frames = archive.read(f"{log_id}.frames.csv")
    except _services.UploadError:
        raise
    except _services.JsonStateError:
        raise _services.UploadError(
            "metadata.json do sidecar inválido ou ambíguo; arquivo preservado",
            transient=False) from None
    except (OSError, _services.zipfile.BadZipFile, KeyError, UnicodeError, RuntimeError, _services.json.JSONDecodeError) as exc:
        raise _services.UploadError(f"sidecar inválido: {exc}", transient=False) from exc
    if not isinstance(metadata, dict):
        raise _services.UploadError("metadata.json do sidecar não é objeto", transient=False)
    if str(metadata.get("logId") or metadata.get("id") or "") != log_id:
        raise _services.UploadError("sidecar pertence a outro log_id", transient=False)
    declared = metadata.get("durationMs")
    if type(declared) is not int or declared <= 0:
        raise _services.UploadError(
            "durationMs do sidecar deve ser inteiro positivo", transient=False)
    tolerance_ms = max(500, int(duration_ms * 0.01))
    if abs(declared - duration_ms) > tolerance_ms:
        raise _services.UploadError(
            f"duração do sidecar diverge do vídeo ({declared} vs {duration_ms} ms)",
            transient=False,
        )
    imu_start, imu_end, _imu_count = _services._csv_span_ns(
        imu, "t", ("t", "ax", "ay", "az", "wx", "wy", "wz"))
    frame_start, frame_end, _frame_count = _services._csv_span_ns(
        frames, "ptsNs", ("i", "ptsNs", "dtNs", "tNs", "key"))
    expected_ns = duration_ms * 1_000_000
    tolerance_ns = max(500_000_000, int(expected_ns * 0.02))
    for kind, actual in (
        ("IMU", imu_end - imu_start),
        ("frames", frame_end - frame_start),
    ):
        if abs(actual - expected_ns) > tolerance_ns:
            raise _services.UploadError(
                f"janela de {kind} diverge do vídeo "
                f"({actual / 1e9:.3f}s vs {duration_ms / 1000:.3f}s)",
                transient=False,
            )
    # Checklist local equivalente ao evaluate (monotonicidade, keyframes,
    # schema do metadata, duração) — QA antes da rede.
    from ..validate import summarize, validate_sidecar_zip
    _summary = summarize(
        validate_sidecar_zip(payload, log_id=log_id, duration_ms=duration_ms))
    if _summary["counts"].get("fail", 0):
        details = "; ".join(
            f"{item['id']}: {item['detail']}"
            for item in _summary["failures"])
        raise _services.UploadError(
            f"sidecar reprovado no checklist local: {details}",
            transient=False,
        )
    return metadata
def _chunk_sidecar(_services, chunk: ChunkResult, session_id: str, org_key: str,
                   task_id: str | None, recorded_at: str, filename: str,
                   state: str, attempts: int, error: str | None) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "org_key": org_key,
        "task_id": task_id,
        "chunk_index": chunk.chunk_index,
        "log_id": chunk.log_id,
        "filename": filename,
        "blob_path": chunk.blob_path,
        "sidecar_blob_path": chunk.sidecar_blob_path,
        "upload_id": chunk.upload_id,
        "size_bytes": chunk.size_bytes,
        "duration_ms": chunk.duration_ms,
        "recorded_at": recorded_at,
        "state": state,
        "attempts": attempts,
        "error": error,
        "updated_at": _services._iso_now(),
    }
def _response_upload_id(_services, data: Any, *, status: int | None = None) -> str:
    """Validate an ID without coercing an invalid response into a URL segment."""
    error = _services.UploadError(
        "Resposta de registro do upload inválida ou sem identificador válido.",
        status_code=status, transient=False, phase="create",
    )
    if not isinstance(data, dict):
        raise error
    values = [data[key] for key in ("id", "uploadId", "upload_id")
              if key in data and data[key] is not None]
    if (not values or any(not isinstance(value, str) or not value
                         or value != value.strip() or value in (".", "..")
                         or any(char.isspace() or ord(char) < 32 or ord(char) == 127
                         or char in "/\\?#%" for char in value)
                         for value in values)
            or any(value != values[0] for value in values[1:])):
        raise error
    return values[0]
def _validated_sas_urls(_services, data: Any, filenames: list[str], *,
                        _native_response_schema: bool = False) -> tuple[dict[str, str], dict[str, str]]:
    """Require all requested artifacts before starting either PUT operation."""
    error = _services.UploadError(
        "Resposta SAS inválida ou incompleta para os arquivos solicitados.",
        status_code=200, transient=False, phase="sas",
    )
    if not isinstance(data, dict):
        raise error
    entries = data.get("signed_urls")
    if not isinstance(entries, list) or not entries:
        raise error
    expected = set(filenames)
    urls: dict[str, str] = {}
    paths: dict[str, str] = {}
    resources: set[tuple[str, str, int, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise error
        filename, url = entry.get("filename"), entry.get("blob_url")
        # APK 1.29 declares expires_at as a required z.string, without a date
        # refinement. Do not infer temporal validity from this structural gate.
        if _native_response_schema and not isinstance(entry.get("expires_at"), str):
            raise error
        if (not isinstance(filename, str) or filename not in expected or filename in urls
                or not isinstance(url, str) or not url or "\\" in url
                or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url)):
            raise error
        try:
            parsed = _services.urllib.parse.urlsplit(url)
            valid = (parsed.scheme == "https" and parsed.hostname
                     and parsed.path and parsed.path != "/"
                     and parsed.username is None and parsed.password is None
                     and not parsed.fragment and parsed.port != 0)
        except ValueError:
            raise error from None
        if not valid:
            raise error
        resource = (parsed.scheme, parsed.hostname, parsed.port or 443, parsed.path)
        if resource in resources:
            raise error
        resources.add(resource)
        urls[filename] = url
        paths[filename] = parsed.path.lstrip("/")
    if set(urls) != expected:
        raise error
    return urls, paths
def _conflict_upload(_services, text: str) -> tuple[dict[str, Any], str]:
    """Read the canonical conflict contract, keeping driver decisions private.

    APK 1.29 requires root upload_id/status and optional string detail. Finite,
    unambiguous JSON and safe, consistent ID aliases are stronger local guards.
    A failed receipt remains an identity to review, never a delivery receipt.
    """
    try:
        body = _services.decode_json_state(text)
    except _services.JsonStateError:
        raise _services.UploadError(
            "POST /uploads conflitante sem JSON válido.",
            status_code=409, transient=False, phase="create",
        ) from None

    status = body.get("status")
    valid_shape = (
        isinstance(body.get("upload_id"), str)
        and isinstance(status, str) and status in ("initiated", "uploaded", "failed")
        and ("detail" not in body or isinstance(body["detail"], str))
    )
    if not valid_shape:
        # Native safeParse succeeds before consulting this exact fallback.
        if body.get("detail") == "Session has been deleted":
            raise _services.UploadError(
                "POST /uploads: a sessão foi removida pelo servidor",
                status_code=409, transient=False, phase="create",
            )
        raise _services.UploadError(
            "POST /uploads conflitante com resposta inválida; preserve o registro para revisão.",
            status_code=409, transient=False, phase="create",
        )

    upload_id = _services._response_upload_id(body, status=409)
    action = {"initiated": "reuse-and-upload", "uploaded": "complete", "failed": "dead-end"}[status]
    # Unknown fields cannot inject a transition or supply an alternative ID.
    return {"id": upload_id, "status": status}, action
