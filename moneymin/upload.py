"""
upload.py — Réplica do fluxo de upload do app Minute (com.bakerdata.minute).

Pipeline de 6 etapas (espelha o fluxo nativo do app Android):
  1. POST  /api/v1/uploads                                  -> registra o chunk
  2. POST  /api/v1/storage/sas/blobs                        -> SAS URLs do Azure
  3. PUT   <blob_url>  (x-ms-blob-type: BlockBlob)          -> bytes direto no Azure
  4. PATCH /api/v1/uploads/{id}/complete                    -> confirma conclusão
  5. POST  /api/v1/uploads/{id}/evaluate  (opcional)         -> verifica qualidade
  6. POST  /api/v1/organizations/{org}/sessions/{sid}/finalize -> confirma sessão

Comportamento nativo adicional (observado no bundle do app e na spec):
  - PATCH /api/v1/uploads/{id}/fail          -> marca o upload como falho (error_message)
  - GET   /api/v1/uploads/{upload_id}        -> consulta status (upload_status)
  - suppress_per_chunk_catbear=True + network_type no PATCH complete (APK 1.29.0)
  - retries limitados nas etapas compatíveis; CREATE sem recibo não é repetido
  - retry-late: após exaurir retries o chunk fica pendente p/ tentativa futura
  - loss record: arquivo local sumiu -> registra perda em vez de abortar
  - sidecar persistente (data/sidecars/<session_id>.json) + fila de retomada

A API nunca toca nos bytes do MP4 — eles vão direto pro Azure Blob Storage.
O registro é criado antes da emissão das URLs SAS nos novos envios, conforme
driveCreate do APK 1.29.0 (S22).
Jornais legados conservam a política de ordem para retomar a etapa pendente.
Cada arquivo _N.mp4 é um chunk de uma mesma sessionId.

Usa `Session` (refresh automático de token) e `config` (URLs/chaves centralizadas).
Somente stdlib.

Exemplo (chunk único):
    from moneymin.minute_api import Session
    from moneymin.upload import upload_session

    sess = Session.from_email("seu@email.com")
    result = upload_session(sess, "data/videos/clip.mp4", org_key="sua_org_key",
                           task_id="uuid-da-task")
    print(result.upload_id)

Exemplo (multi-chunk):
    result = upload_session(sess, ["chunk0.mp4", "chunk1.mp4"], org_key="...",
                           task_id="...")

Exemplo (fila com retomada):
    from moneymin.upload import enqueue_upload, pump_pending

    enqueue_upload(sess, "data/videos/clip.mp4", org_key="...", task_id="...")
    pump_pending(sess)   # processa sidecars pendentes (retry-late / loss)
"""
from __future__ import annotations

import io
import hashlib
import json
import random
import re
import time
import urllib.error
import urllib.parse
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import config, transport
from .campaign_state import campaign_state_operation
from .atomic_io import JsonStateError, decode_json_state, load_json_state, save_bytes, save_json
from .capture_import import CaptureDescriptor, CaptureImportError, inspect_original_capture
from .device_profile import (
    BACKLOG_CAP_MS,
    DeviceProfile,
    format_recorded_at,
    recorded_at_to_wall_ms,
    recording_start_epoch,
)
from .sidecar import (
    build_metadata_json,
    build_sidecar_zip,
    ffmpeg_bin,
    probe_video,
)
from .upload_types import (
    STATE_COMPLETING,
    STATE_CREATING,
    STATE_DONE,
    STATE_FAILED,
    STATE_LOSS,
    STATE_QUARANTINE,
    STATE_RETRY_LATE,
    STATE_TRANSPORT,
    TRANSIENT_STATES,
    ChunkResult,
    UploadError,
    UploadResult,
    is_pending_finalization,
    is_pending_evaluation,
    journal_delivery_confirmed,
    journal_flags_valid,
)
from .delivery import quality as _quality
from .delivery import receipts as _receipts
from .delivery import reconciliation as _reconciliation
from .delivery import media as _delivery_media
from .delivery import protocol as _delivery_protocol
from .delivery import journal as _delivery_journal
from .delivery import chunk as _delivery_chunk
from .delivery import session as _delivery_session
from .delivery import recovery as _delivery_recovery

__all__ = [
    "ChunkResult",
    "STATE_COMPLETING",
    "STATE_CREATING",
    "STATE_DONE",
    "STATE_FAILED",
    "STATE_LOSS",
    "STATE_QUARANTINE",
    "STATE_RETRY_LATE",
    "STATE_TRANSPORT",
    "TRANSIENT_STATES",
    "UploadError",
    "UploadResult",
    "enqueue_upload",
    "is_pending_transport",
    "is_terminal_remote_failure",
    "all_terminal_remote_failure",
    "is_remote_recovery_candidate",
    "validate_remote_recovery_receipt",
    "mark_remote_terminal_failure",
    "pump_pending",
    "trim_video",
    "upload_session",
    "upload_video",
]

UPLOAD_MIN_DURATION_MS = 60_000
UPLOAD_MAX_DURATION_MS = 1_800_000


# --- Metadados de dispositivo/plataforma/video/rede (mimica app nativo) ---------

def default_device_meta(profile: DeviceProfile | None=None) -> dict[str, Any]:
    'Metadados de dispositivo que o app nativo envia em meta.device.\n\n    Formato curto Android (getDeviceUploadMeta): APENAS {"model": Build.MODEL}.\n    NÃO envia systemName nem systemVersion no POST /uploads.\n    Com perfil: o Build.MODEL Samsung daquele aparelho, não o default global.\n    '
    return _delivery_media.default_device_meta(_media_services(), profile)



def default_platform_meta(profile: DeviceProfile | None=None) -> dict[str, Any]:
    'Metadados de plataforma que o app nativo envia em meta.platform.\n\n    Formato curto Android (getDeviceUploadMeta): APENAS {"os": "android"}.\n    NÃO envia version no POST /uploads.\n    '
    return _delivery_media.default_platform_meta(_media_services(), profile)



def _iso_now() -> str:
    return _delivery_media._iso_now(_media_services())



def _normalize_recorded_at_sequence(values: list[str], durations_ms: list[int], *, now: float | None=None) -> list[str]:
    'Canoniza horários explícitos sem destruir a distância entre chunks.\n\n    O chamador já usou esses mesmos horários para montar os sidecars. Portanto\n    não podemos prender cada chunk à duração total da sessão: isso colapsa os\n    últimos horários e faz o POST divergir de ``metadata.createdAt``. Valores\n    explícitos são validados como uma sequência e apenas reformatados.\n    '
    return _delivery_media._normalize_recorded_at_sequence(_media_services(), values, durations_ms, now=now)



def _recorded_at_sequence_from_base(base: str, durations_ms: list[int], *, now: float | None=None) -> list[str]:
    'Expande o início da sessão em inícios contínuos de cada chunk.'
    return _delivery_media._recorded_at_sequence_from_base(_media_services(), base, durations_ms, now=now)



def default_video_meta() -> dict[str, Any]:
    'Metadados de vídeo que o app nativo envia em meta.video.\n\n    Shape Android: {height, path, rotationDeg, width} (path = {logId}.mp4).\n    '
    return _delivery_media.default_video_meta(_media_services())



def default_network_meta() -> dict[str, Any]:
    'Metadados de rede que o app nativo envia em meta.network.'
    return _delivery_media.default_network_meta(_media_services())



# --- HTTP para o Azure Blob (PUT direto, fora da minute-api) -------------------

def _put_blob(blob_url: str, file_bytes: bytes, content_type: str='video/mp4', timeout: int=300, resumable: bool | None=None, on_progress: Callable[[int, int, float], None] | None=None) -> int:
    'Faz PUT dos bytes no Azure Blob Storage. Devolve o status HTTP.\n\n    O transporte (e o fingerprint de rede) é do `transport.py`: com curl_cffi\n    o MP4 e o sidecar sobem em blocos de 4MB + Put Block List, como o\n    AzureBlockUploader/OkHttp do app Android. Sem curl_cffi, PUT BlockBlob\n    único. Sucesso é 201 Created (o OkHttp do app aceita qualquer 2xx).\n\n    Levanta UploadError se o Azure recusar (status != 201) ou em erro de rede.\n    '
    return _delivery_media._put_blob(_media_services(), blob_url, file_bytes, content_type, timeout, resumable, on_progress)



def _put_blob_file(blob_url: str, file_path: str | Path, content_type: str='video/mp4', timeout: int=300, on_progress: Callable[[int, int, float], None] | None=None, *, expected_sha256: str | None=None) -> int:
    'PUT do vídeo a partir do disco, sem uma cópia integral na RAM.'
    return _delivery_media._put_blob_file(_media_services(), blob_url, file_path, content_type, timeout, on_progress, expected_sha256=expected_sha256)



def _probe_duration_ms(video_path: str | Path) -> int:
    'Duração em ms: ffprobe, senão `ffmpeg -i` (imageio), senão PyAV.'
    return _delivery_media._probe_duration_ms(_media_services(), video_path)



# --- Normalização de vídeo (full-range -> limited-range) -----------------------

def _video_pix_fmt(video_path: str | Path) -> tuple[str | None, str | None]:
    'pix_fmt / color_range via probe unificado (ffprobe ou ffmpeg -i).'
    return _delivery_media._video_pix_fmt(_media_services(), video_path)



def _video_handler(video_path: str | Path) -> str | None:
    "handler_name do stream de vídeo (ex.: 'VideoHandler')."
    return _delivery_media._video_handler(_media_services(), video_path)



def _run_ffmpeg(cmd: list[str], timeout: int=3600):
    'Roda ffmpeg sem stdin (evita pausa) e sem console no Windows.'
    return _delivery_media._run_ffmpeg(_media_services(), cmd, timeout)



def normalize_video(video_path: str | Path, out_dir: str | Path | None=None, preset: str='veryfast') -> Path:
    'Reencoda o vídeo para o formato EGO nativo do app Android (1440x1080 4:3).\n\n    O app grava a ultra-wide em 1440x1080 (4:3); datasets/YouTube vêm em\n    16:9 (1920x1080). O Catbear pontua melhor (clarity=great) o que replica a\n    gravação Android: proporção 4:3, yuv420p limited-range, H.264 High@4.2\n    (MediaRecorder setVideoEncodingProfileLevel(8, 8192)), handler\n    VideoHandler/SoundHandler e áudio AAC 48 kHz estéreo 256 kbps.\n\n    Conversão: escala para cobrir 1440x1080 (force_original_aspect_ratio=increase)\n    e corta o excesso (crop) para 4:3 — conteúdo central preservado.\n\n    Devolve o caminho pronto para upload (o próprio arquivo se já for\n    compatível, ou um arquivo `_yuv420p.mp4` ao lado).\n    Levanta UploadError se precisar reencodar e ffmpeg faltar/falhar.\n    '
    return _delivery_media.normalize_video(_media_services(), video_path, out_dir, preset)



def _video_dims(video_path: str | Path) -> tuple[int, int]:
    'Resolução (width, height) do stream de vídeo.'
    return _delivery_media._video_dims(_media_services(), video_path)



def trim_video(video_path: str | Path, cut_first: float=30.0, cut_last: float=30.0, auto: bool=True, out_dir: str | Path | None=None, margin: float=1.0) -> str:
    'Corta intro/outro de vídeos (30s início + 30s fim por padrão).\n\n    Padrão: remove 30 segundos do início e 30 segundos do fim — prático,\n    rápido e seguro para vídeos de YouTube que sempre têm intro/outro.\n\n    Devolve o caminho do vídeo cortado (o próprio arquivo se não precisou corte,\n    ou um arquivo _trimmed.mp4 ao lado).\n    '
    return _delivery_media.trim_video(_media_services(), video_path, cut_first, cut_last, auto, out_dir, margin)



# --- Retry com backoff (mimica o auto-retry do app) ----------------------------

def _session_request(session: Any, method: str, path: str, body: Any=None) -> tuple[int, str, dict[str, str]]:
    'Usa headers de resposta quando a sessão oferece a API detalhada.'
    return _delivery_protocol._session_request(_protocol_services(), session, method, path, body)



def _header(headers: dict[str, str], name: str) -> str | None:
    return _delivery_protocol._header(_protocol_services(), headers, name)



def _error_detail(text: str) -> str:
    return _delivery_protocol._error_detail(_protocol_services(), text)



def _http_upload_error(phase: str, status: int, text: str, headers: dict[str, str] | None=None) -> UploadError:
    return _delivery_protocol._http_upload_error(_protocol_services(), phase, status, text, headers)



def _status_from_exception(exc: BaseException) -> int | None:
    return _delivery_protocol._status_from_exception(_protocol_services(), exc)


def _with_retry(fn: Callable[[], Any], *, max_retries: int=3, retry_backoff: float=1.5, retry_delay: float=1.0, label: str='etapa') -> tuple[Any, int]:
    'Executa `fn` com auto-retry em falhas transientes.\n\n    O app nativo usa "auto-retry" para falhas de create/sas, transport e sidecar\n    (strings de log: `[upload] transient ... -> auto-retry`). Aqui replicamos com\n    backoff exponencial: 1s, 1.5s, 2.25s... Devemos exaurir os retries.\n    Retorna (resultado, tentativas_usadas).\n    Levanta a última UploadError depois de `max_retries` tentativas.\n    '
    return _delivery_protocol._with_retry(_protocol_services(), fn, max_retries=max_retries, retry_backoff=retry_backoff, retry_delay=retry_delay, label=label)



# --- Etapa extra nativa: marcar upload como falho ------------------------------

def fail_upload(session: Any, upload_id: str, error_message: str) -> dict[str, Any]:
    'PATCH /api/v1/uploads/{id}/fail — marca o upload como falho no backend.\n\n    O app nativo usa isso quando o upload falha após o registro (`_failUpload`).\n    O backend guarda `error_message` (max 500 chars) no registro.\n    Retorna o UploadOut parseado. Levanta UploadError se o backend recusar.\n    '
    return _delivery_protocol.fail_upload(_protocol_services(), session, upload_id, error_message)



def delete_upload(session: Any, upload_id: str) -> bool:
    'DELETE /api/v1/uploads/{upload_id} — remove o upload do backend.\n\n    Usado pela política perfect-only: se o evaluate reprovar, o registro de\n    upload é apagado (rows + referência). Sucesso = 204 No Content.\n    Retorna True se deletado.\n    '
    return _delivery_protocol.delete_upload(_protocol_services(), session, upload_id)



def delete_session(session: Any, org_key: str, session_id: str) -> bool:
    'DELETE /api/v1/organizations/{org}/sessions/{sid} — remove a sessão.\n\n    Apaga a sessão de gravação (rows + blobs do Azure) para o caller.\n    Sucesso = 204 No Content. Retorna True se deletada.\n    '
    return _delivery_protocol.delete_session(_protocol_services(), session, org_key, session_id)



def _validate_known_receipt(data: dict[str, Any], upload_id: str, phase: str) -> None:
    'An optional legacy ID must still refer to the requested receipt.\n\n    Empty legacy acknowledgements remain supported. This does not implement\n    the complete native UploadOut schema or prove remote acceptance.\n    '
    return _delivery_protocol._validate_known_receipt(_protocol_services(), data, upload_id, phase)



def _validate_native_upload_out(data: dict[str, Any], phase: str) -> None:
    'UploadOut declared in APK 1.29 (decompiled.js L357468); status is a string.\n\n    Preserve finite extra metadata for review. blob_path belongs to meta and,\n    when present, must be a string; an absent field differs from JSON null.\n    A structurally valid response alone does not prove physical capture quality.\n    '
    return _delivery_protocol._validate_native_upload_out(_protocol_services(), data, phase)



def get_upload(session: Any, upload_id: str) -> dict[str, Any]:
    'GET /api/v1/uploads/{upload_id} — consulta o status do upload.\n\n    O app nativo consulta o registro para saber o estado (`upload_status`).\n    Devolve o dict com campos camelCase (uploadId, sessionId, logId, status,\n    durationMs, recordedAt, ...). Levanta UploadError se não achar (404).\n    '
    return _delivery_protocol.get_upload(_protocol_services(), session, upload_id)



def _receipt_services() -> _receipts.ReceiptServices:
    """Provide live compatibility callbacks to the isolated receipt protocol."""
    return _receipts.ReceiptServices(
        upload_error=UploadError,
        sidecar_filename=_sidecar_filename,
        response_upload_id=_response_upload_id,
        journal_recorded_at=_journal_recorded_at,
        journal_flags_valid=journal_flags_valid,
        recorded_at_to_wall_ms=recorded_at_to_wall_ms,
        format_recorded_at=format_recorded_at,
        iso_now=_iso_now,
        is_pending_transport=is_pending_transport,
        remote_recovery_identity=_remote_recovery_identity,
        session_resource_identity=_session_resource_identity,
        validate_remote_recovery_receipt=validate_remote_recovery_receipt,
        terminal_failure_marker=_terminal_failure_marker,
        is_terminal_remote_failure=is_terminal_remote_failure,
    )


def _remote_recovery_identity(row: dict[str, Any]) -> dict[str, Any]:
    return _receipts._remote_recovery_identity(row, _receipt_services())


def _session_resource_identity(
    session: Any, owner_resource_key: str | None = None,
) -> tuple[str, str]:
    return _receipts._session_resource_identity(
        session, _receipt_services(), owner_resource_key)


def validate_remote_recovery_receipt(
    row: dict[str, Any], receipt: Any, session: Any, *,
    owner_resource_key: str | None = None,
) -> str:
    return _receipts.validate_remote_recovery_receipt(
        row, receipt, session, _receipt_services(), owner_resource_key=owner_resource_key)


def _terminal_failure_marker(row: dict[str, Any]) -> dict[str, Any]:
    return _reconciliation._terminal_failure_marker(row, _receipt_services())


def is_terminal_remote_failure(row: Any) -> bool:
    return _reconciliation.is_terminal_remote_failure(row, _receipt_services())


def mark_remote_terminal_failure(
    row: dict[str, Any], receipt: Any, session: Any, *,
    checked_at: str | None = None, owner_resource_key: str | None = None,
) -> dict[str, Any]:
    return _reconciliation.mark_remote_terminal_failure(
        row, receipt, session, _receipt_services(), checked_at=checked_at,
        owner_resource_key=owner_resource_key)


def all_terminal_remote_failure(rows: Any) -> bool:
    return _reconciliation.all_terminal_remote_failure(rows, _receipt_services())


def is_remote_recovery_candidate(row: Any) -> bool:
    return _reconciliation.is_remote_recovery_candidate(row, _receipt_services())


def _require_completion_flags(suppress_per_chunk_catbear: bool, session_complete: bool=False) -> None:
    return _delivery_protocol._require_completion_flags(_protocol_services(), suppress_per_chunk_catbear, session_complete)



def _journal_flag(journal: dict[str, Any], key: str) -> bool:
    'Absence is the legacy False policy; never coerce an existing value.'
    return _delivery_protocol._journal_flag(_protocol_services(), journal, key)



def _journal_recorded_at(journal: dict[str, Any]) -> str:
    return _delivery_protocol._journal_recorded_at(_protocol_services(), journal)



def _journal_has_remote_receipt(journal: dict[str, Any]) -> bool:
    return _delivery_protocol._journal_has_remote_receipt(_protocol_services(), journal)



def _existing_receipt_error() -> UploadError:
    return _delivery_protocol._existing_receipt_error(_protocol_services())



def _journal_creation_uncertain(journal: dict[str, Any]) -> bool:
    'No receipt does not prove that a previously attempted CREATE failed.\n\n    Legacy queued journals precede any remote creation. Other legacy phases\n    lack a durable request marker, so cannot authorize another CREATE safely.\n    Known receipts retain their existing completion/finalization handling.\n    '
    return _delivery_protocol._journal_creation_uncertain(_protocol_services(), journal)



def _uncertain_creation_error() -> UploadError:
    return _delivery_protocol._uncertain_creation_error(_protocol_services())



def _upload_owner(session: Any, profile: DeviceProfile | None) -> str:
    "Use the session's declared owner, never infer auth from device metadata."
    return _delivery_protocol._upload_owner(_protocol_services(), session, profile)



def _validate_journal_owner(journal: dict[str, Any], session_owner: str) -> None:
    return _delivery_protocol._validate_journal_owner(_protocol_services(), journal, session_owner)



def complete_upload(session: Any, upload_id: str, size_bytes: int, *, suppress_per_chunk_catbear: bool=True, session_complete: bool=False, network_type: str='wifi', _native_response_schema: bool=False) -> dict[str, Any]:
    'Confirma um blob já enviado; operação reutilizável após reinício.\n\n    No APK 1.29.0, ``_completeUpload`` envia sempre ``suppress_per_chunk_catbear``\n    e ``network_type`` (wifi|cellular); ``session_complete`` só no último chunk.\n    O QMoney usa ``complete -> finalize`` em etapas separadas. Sem prova de\n    aceitação remota pelo Catbear.\n    '
    return _delivery_protocol.complete_upload(_protocol_services(), session, upload_id, size_bytes, suppress_per_chunk_catbear=suppress_per_chunk_catbear, session_complete=session_complete, network_type=network_type, _native_response_schema=_native_response_schema)



# --- Sidecar persistente + fila (mimica recording.saveBody / pumpUploads) ------

def sidecars_dir() -> Path:
    'Retomadas pertencem à instalação, não à biblioteca compartilhável.'
    return _delivery_journal.sidecars_dir(_journal_services())



MAX_CRASH_RESUMES = 3


def _sidecar_filename(session_id: str, chunk_index: int=0) -> str:
    return _delivery_journal._sidecar_filename(_journal_services(), session_id, chunk_index)



def _sidecar_path(session_id: str, chunk_index: int=0) -> Path:
    return _delivery_journal._sidecar_path(_journal_services(), session_id, chunk_index)



def _sidecar_archive_path(session_id: str, chunk_index: int=0) -> Path:
    return _delivery_journal._sidecar_archive_path(_journal_services(), session_id, chunk_index)



@campaign_state_operation
def _remove_sidecar_archive(session_id: str, chunk_index: int=0) -> None:
    'Remove somente o ZIP temporário depois da entrega confirmada.'
    return _delivery_journal._remove_sidecar_archive(_journal_services(), session_id, chunk_index)



def _sidecar_resume_payload(item: dict[str, Any]) -> bytes:
    'Bind the original archive to a known uploaded receipt before effects.\n\n    This is an integrity/identity check, not proof of physical sensor origin.\n    Missing historical bindings require review rather than generating new data.\n    '
    return _delivery_journal._sidecar_resume_payload(_journal_services(), item)



_CURL_TRANSPORT_RETRY_CODES = frozenset({6, 28, 52, 56})
_CURL_ERROR_CODE = re.compile(r"(?:curl|libcurl)\s*:\s*\((\d+)\)", re.IGNORECASE)


def is_pending_transport(item: Any) -> bool:
    'Cheaply recognize a known-receipt video transport that can be resumed.\n\n    File hashes and the exact archived ZIP are revalidated by the transport\n    driver before its first checkpoint or network call. A legacy failed row is\n    eligible only for known transient libcurl codes; arbitrary failed CREATE,\n    SAS, and HTTP rows remain manual-review cases.\n    '
    return _delivery_journal.is_pending_transport(_journal_services(), item)



def _video_resume_payload(item: dict[str, Any], profile: DeviceProfile | None) -> tuple[Path, bytes]:
    'Validate the immutable MP4/ZIP/profile binding before resuming both PUTs.'
    return _delivery_journal._video_resume_payload(_journal_services(), item, profile)



@campaign_state_operation
def save_sidecar(sidecar: dict[str, Any]) -> Path:
    'Persiste o estado de um upload em `data/sidecars/<session_id>.json`.'
    return _delivery_journal.save_sidecar(_journal_services(), sidecar)



def _read_sidecar_file(path: Path) -> dict[str, Any] | None:
    'One authoritative decoder and filename contract for every journal reader.'
    return _delivery_journal._read_sidecar_file(_journal_services(), path)



def _load_sidecar_from_path(path: Path, session_id: str, chunk_index: int) -> dict[str, Any] | None:
    'Decode one resolved journal without repeating storage migration.'
    return _delivery_journal._load_sidecar_from_path(_journal_services(), path, session_id, chunk_index)



@campaign_state_operation
def load_sidecar(session_id: str, chunk_index: int=0) -> dict[str, Any] | None:
    'Lê um recibo; somente a ausência real retorna None.'
    return _delivery_journal.load_sidecar(_journal_services(), session_id, chunk_index)



@campaign_state_operation
def list_sidecars(state: str | None=None) -> list[dict[str, Any]]:
    'Valida todos os journals antes de retornar o filtro de estado.'
    return _delivery_journal.list_sidecars(_journal_services(), state)



def _csv_span_ns(payload: bytes, timestamp_column: str, required_columns: tuple[str, ...]) -> tuple[int, int, int]:
    'Valida um CSV temporal e devolve primeira/última marca e nº de amostras.'
    return _delivery_journal._csv_span_ns(_journal_services(), payload, timestamp_column, required_columns)



def _validate_sidecar_zip(payload: bytes, *, log_id: str, duration_ms: int) -> dict[str, Any]:
    'Falha antes da rede se ZIP, metadata, IMU e frames forem incoerentes.'
    return _delivery_journal._validate_sidecar_zip(_journal_services(), payload, log_id=log_id, duration_ms=duration_ms)



def _chunk_sidecar(chunk: ChunkResult, session_id: str, org_key: str, task_id: str | None, recorded_at: str, filename: str, state: str, attempts: int, error: str | None) -> dict[str, Any]:
    return _delivery_journal._chunk_sidecar(_journal_services(), chunk, session_id, org_key, task_id, recorded_at, filename, state, attempts, error)



def _response_upload_id(data: Any, *, status: int | None=None) -> str:
    'Validate an ID without coercing an invalid response into a URL segment.'
    return _delivery_journal._response_upload_id(_journal_services(), data, status=status)



def _validated_sas_urls(data: Any, filenames: list[str], *, _native_response_schema: bool=False) -> tuple[dict[str, str], dict[str, str]]:
    'Require all requested artifacts before starting either PUT operation.'
    return _delivery_journal._validated_sas_urls(_journal_services(), data, filenames, _native_response_schema=_native_response_schema)



def _conflict_upload(text: str) -> tuple[dict[str, Any], str]:
    'Read the canonical conflict contract, keeping driver decisions private.\n\n    APK 1.29 requires root upload_id/status and optional string detail. Finite,\n    unambiguous JSON and safe, consistent ID aliases are stronger local guards.\n    A failed receipt remains an identity to review, never a delivery receipt.\n    '
    return _delivery_journal._conflict_upload(_journal_services(), text)



# --- Fluxo de um chunk individual (etapas 1-4) --------------------------------

def _upload_single_chunk(session: Any, video_path: Path, org_key: str, session_id: str, chunk_index: int, task_id: str | None, content_type: str, timeout_blob: int, recorded_at: str, device_meta: dict[str, Any] | None, platform_meta: dict[str, Any] | None, video_meta: dict[str, Any] | None, network_meta: dict[str, Any] | None, max_retries: int=3, retry_backoff: float=1.5, suppress_per_chunk_catbear: bool=True, session_complete: bool=False, fail_on_error: bool=True, sidecar: bool=True, sidecar_data: bytes | None=None, ego_meta: dict[str, Any] | None=None, register_first: bool=True, on_progress: Callable[..., None] | None=None, profile: DeviceProfile | None=None, checkpoint: Callable[..., None] | None=None, _resume_sidecar_row: dict[str, Any] | None=None, _native_response_schema: bool=True, _original_capture: CaptureDescriptor | None=None, _expected_video_sha256: str | None=None) -> ChunkResult:
    'Executa registro -> SAS -> PUT Blob -> PATCH /complete.\n\n    Novos envios usam a ordem observada em driveCreate do APK 1.29.0:\n    POST /uploads -> SAS -> transporte -> complete. False é preservado para\n    retomadas explicitamente identificadas com a ordem legada.\n\n    Espelha o app nativo:\n      - auto-retry com backoff nas etapas transientes (create/sas, transport);\n      - PATCH complete com supressão True nos novos envios; False explícito\n        mantém a omissão legada. `session_complete` só é enviado quando esse\n        parâmetro é True; não há equivalência demonstrada entre o desktop e\n        os estados Android accepted && saveGated (U04 permanece aberto);\n      - se o registro foi criado e a confirmação falhar, marca `PATCH /fail`\n        (fail_on_error) — comportamento `_failUpload` do app;\n      - se o arquivo local sumir, devolve um ChunkResult em estado `loss`\n        (loss record) em vez de abortar a sessão.\n      - (sidecar=True) gera e sobe o `.data.zip` nativo junto com o MP4\n        (imu.csv, frames.csv, metadata.json) e envia meta ego\n        (source/timebase/cameras/codecActuals) — requisito do evaluate.\n\n    Retorna o ChunkResult com os IDs e metadados. Política de ordem não booleana\n    conserva o contrato de retorno STATE_FAILED e comunica somente o diagnóstico\n    ao callback checkpoint, antes de probe/HTTP. Esse callback é código do\n    chamador e pode produzir efeitos locais. Flags de conclusão não booleanas\n    levantam UploadError antes de qualquer callback.\n    '
    return _delivery_chunk._upload_single_chunk(_chunk_services(), session, video_path, org_key, session_id, chunk_index, task_id, content_type, timeout_blob, recorded_at, device_meta, platform_meta, video_meta, network_meta, max_retries, retry_backoff, suppress_per_chunk_catbear, session_complete, fail_on_error, sidecar, sidecar_data, ego_meta, register_first, on_progress, profile, checkpoint, _resume_sidecar_row, _native_response_schema, _original_capture, _expected_video_sha256)



# --- Etapa 5: Finalize sessão -------------------------------------------------

def _finalize_session(session: Any, org_key: str, session_id: str, expected_chunk_count: int) -> tuple[bool, int]:
    'POST /api/v1/organizations/{org}/sessions/{sid}/finalize.\n\n    Marca a sessão como completa e elegível para catbear (avaliação de qualidade).\n    Retorna (sucesso, status_http). Sucesso é 204 No Content.\n    '
    return _delivery_protocol._finalize_session(_protocol_services(), session, org_key, session_id, expected_chunk_count)



# --- Etapa 6: Evaluate (opcional) --------------------------------------------

def _quality_services() -> _quality.QualityServices:
    """Supply live facade collaborators so legacy patches remain effective."""
    return _quality.QualityServices(
        upload_error=UploadError,
        json_state_error=JsonStateError,
        decode_json_state=decode_json_state,
        with_retry=_with_retry,
        evaluate_upload=evaluate_upload,
        summarize_checks=summarize_checks,
        is_perfect=is_perfect,
        fail_upload=fail_upload,
        delete_upload=delete_upload,
        delete_session=delete_session,
        session_request=_session_request,
        http_upload_error=_http_upload_error,
    )


def evaluate_upload(session: Any, upload_id: str) -> dict[str, Any]:
    return _quality.evaluate_upload(session, upload_id, _quality_services())


def summarize_checks(evaluation: dict[str, Any]) -> dict[str, Any]:
    return _quality.summarize_checks(evaluation)


def is_perfect(evaluation: dict[str, Any]) -> bool:
    return _quality.is_perfect(evaluation, summarize=summarize_checks)


def require_perfect_upload(
    session: Any,
    upload_id: str,
    org_key: str,
    session_id: str,
    *,
    fail_on_error: bool = True,
    delete_on_fail: bool = True,
) -> dict[str, Any]:
    return _quality.require_perfect_upload(
        session, upload_id, org_key, session_id, _quality_services(),
        fail_on_error=fail_on_error, delete_on_fail=delete_on_fail,
    )


# --- Fluxo completo (sessão com 1+ chunks) -----------------------------------

def _original_group_payloads(captures: list[CaptureDescriptor] | tuple[CaptureDescriptor, ...], paths: list[Path], *, session: Any, owner: str, org_key: str, task_id: str | None, session_id: str | None, recorded_at: str | list[str] | None, supplied: bytes | list[bytes] | None) -> tuple[list[CaptureDescriptor], list[bytes], list[int], list[str]]:
    'Reinspect all parts before journaling/HTTP; never repair source data.'
    return _delivery_session._original_group_payloads(_session_services(), captures, paths, session=session, owner=owner, org_key=org_key, task_id=task_id, session_id=session_id, recorded_at=recorded_at, supplied=supplied)



def _verify_video_content_digest(path: Path, digest: Any) -> None:
    return _delivery_session._verify_video_content_digest(_session_services(), path, digest)



from .upload_protocol_lease import session_protocol, pending_protocol


@session_protocol
def upload_session(session: Any, video_path: str | Path | list[str | Path], org_key: str, task_id: str | None=None, session_id: str | None=None, recorded_at: str | list[str] | None=None, content_type: str='video/mp4', timeout_blob: int=300, finalize: bool=True, evaluate: bool=False, device_meta: dict[str, Any] | None=None, platform_meta: dict[str, Any] | None=None, video_meta: dict[str, Any] | None=None, network_meta: dict[str, Any] | None=None, max_retries: int=3, retry_backoff: float=1.5, suppress_per_chunk_catbear: bool=True, fail_on_error: bool=True, persist_sidecar: bool=False, sidecar: bool=True, sidecar_data: bytes | list[bytes] | None=None, ego_meta: dict[str, Any] | None=None, require_perfect: bool=False, normalize: bool=True, register_first: bool=True, on_progress: Callable[..., None] | None=None, profile: DeviceProfile | None=None, chunk_index_start: int=0, campaign_context: dict[str, Any] | None=None, original_captures: list[CaptureDescriptor] | tuple[CaptureDescriptor, ...] | None=None, expected_video_sha256: str | list[str] | None=None) -> UploadResult:
    'Executa o upload completo de uma sessão (1+ chunks) ao backend do Minute.\n\n    Parâmetros:\n        session       — `moneymin.minute_api.Session` autenticada.\n        video_path    — caminho do vídeo (str/Path) ou lista de caminhos (multi-chunk).\n        org_key       — resourceKey da organização destino.\n        task_id       — UUID da task do Minute (opcional, liga o upload à task).\n        session_id    — ID da sessão de gravação (UUID v4 gerado se não informado).\n        content_type  — MIME type do vídeo (default video/mp4).\n        timeout_blob  — timeout em segundos para o PUT no Azure (default 300).\n        finalize      — se True, chama POST /sessions/{sid}/finalize ao final.\n        evaluate      — se True, chama POST /uploads/{id}/evaluate em cada chunk.\n        device_meta   — metadados de dispositivo (default: Pixel 8 Pro / Android 14).\n        platform_meta — metadados de plataforma (default: android / app_version).\n        video_meta    — metadados de vídeo (default: h264 / 1080p / 30fps).\n        network_meta  — metadados de rede (default: wifi).\n        max_retries   — limite de tentativas nas etapas compatíveis (default3).\n                      CREATE sem recibo usa uma tentativa; falha mantém o\n                      registro para revisão, sem presumir idempotência remota.\n        retry_backoff — multiplicador do backoff entre retries (default 1.5).\n        suppress_per_chunk_catbear — True em envios novos, como no helper do APK\n                      1.29.0, independentemente da contagem de chunks. False\n                      explícito mantém a omissão legada. Journal existente governa\n                      a política efetiva: valor booleano salvo ou False se ausente.\n        fail_on_error — se True, marca PATCH /fail quando o registro já existe\n                      e a confirmação falha (comportamento _failUpload do app).\n        persist_sidecar — se True, grava data/sidecars/<session_id>.json com o\n                      estado de cada chunk (permite retomada via pump_pending).\n        sidecar       — se True (default), gera e sobe o `.data.zip` nativo\n                      (imu.csv/frames.csv/metadata.json) junto com o MP4 e envia\n                      meta ego (source/timebase/cameras/codecActuals). Requisito\n                      do evaluate do backend.\n        require_perfect — política perfect-only: após o upload, roda o evaluate\n                      e, se QUALQUER check falhar, marca PATCH /fail e DELETA o\n                      upload + a sessão no backend (DELETE /uploads/{id} e\n                      DELETE /sessions/{sid}). Só finaliza a sessão se perfeita.\n        profile       — perfil de APARELHO da conta (device_profile). Com ele,\n                      o sidecar usa calibração/uptime próprios e o meta usa o\n                      modelo daquele aparelho (anti-colusão). Sem ele, usa o\n                      Samsung padrão de referência (config.NATIVE_*).\n        chunk_index_start — índice inicial do chunk; usado pela retomada para\n                      manter a identidade original e evitar registros duplicados.\n        on_progress   — callback síncrono(phase, state, attempt) no fluxo padrão, antes de\n                      CREATE, SAS, PUT de vídeo/ZIP, COMPLETE, EVALUATE,\n                      FINALIZE e compensação /fail, incluindo novas tentativas\n                      dessas etapas. O chamador pode aguardar Retomar nele.\n                      O progresso do transporte consulta o chamador antes de\n                      cada bloco/tentativa e BlockList, inclusive no ZIP.\n                      Requisições já em andamento podem terminar.\n                      A política require_perfect tem compensações próprias.\n\n    Devolve `UploadResult` com os IDs, metadados e status de finalize/evaluate.\n    Levanta `UploadError` em qualquer falha.\n    '
    return _delivery_session.upload_session(_session_services(), session, video_path, org_key, task_id, session_id, recorded_at, content_type, timeout_blob, finalize, evaluate, device_meta, platform_meta, video_meta, network_meta, max_retries, retry_backoff, suppress_per_chunk_catbear, fail_on_error, persist_sidecar, sidecar, sidecar_data, ego_meta, require_perfect, normalize, register_first, on_progress, profile, chunk_index_start, campaign_context, original_captures, expected_video_sha256)



# --- Compatibilidade: upload_video (chunk único, interface antiga) ------------

def upload_video(session: Any, video_path: str | Path, org_key: str, task_id: str | None=None, session_id: str | None=None, log_id: str | None=None, content_type: str='video/mp4', chunk_index: int=0, timeout_blob: int=300) -> UploadResult:
    'Compatibilidade com a interface antiga de chunk único.\n\n    Usa upload_session internamente. Se log_id for fornecido, extrai o\n    session_id e chunk_index dele (formato: {sessionId}_{chunkIndex}).\n\n    Devolve `UploadResult` (use .upload_id, .blob_path, .log_id para os\n    valores do primeiro/único chunk).\n    '
    return _delivery_session.upload_video(_session_services(), session, video_path, org_key, task_id, session_id, log_id, content_type, chunk_index, timeout_blob)



# --- Fila de retomada (mimica pumpUploadsScreen do app) ------------------------

def enqueue_upload(session: Any, video_path: str | Path, org_key: str, task_id: str | None=None, session_id: str | None=None, content_type: str='video/mp4', **kwargs: Any) -> UploadResult:
    'Executa o upload e persiste o sidecar — o upload entra na "fila".\n\n    Depois de enfileirar, `pump_pending()` pode ser chamado para tentar de novo\n    os chunks que ficaram em retry_late/loss/failed (comportamento do app de\n    retomar uploads pendentes ao reabrir / voltar online).\n    '
    return _delivery_recovery.enqueue_upload(_recovery_services(), session, video_path, org_key, task_id, session_id, content_type, **kwargs)



def _pending_recovery_stage(item: dict[str, Any]) -> str:
    'Validate a selected journal before effects; shared with recovery UI.'
    return _delivery_recovery._pending_recovery_stage(_recovery_services(), item)



@pending_protocol
def pump_pending(session: Any, state: str | None=None, max_retries: int=3, retry_backoff: float=1.5, account_email: str | None=None, required_org_key: str | None=None, session_ids: set[str] | None=None, **kwargs: Any) -> list[dict[str, Any]]:
    'Processa sidecars pendentes (retry-late / loss / failed) e tenta de novo.\n\n    Mimica `pumpUploadsScreen` / "SeeUploads resume when you\'re back online" do\n    app: os uploads que falharam de forma transiente ficam persistidos e são\n    retomados numa execução posterior.\n\n    Para cada sidecar pendente, chama upload_session com o MESMO session_id e\n    chunk_index, preservando a identidade do envio. Se o blob já chegou, retoma\n    somente complete/finalize, sem reenviar o vídeo. O vídeo local precisa existir\n    para retomadas anteriores ao transporte. Quando account_email é informado,\n    somente journals daquela conta são processados. Com required_org_key,\n    pendências de outras organizações ficam preservadas sem reenvio.\n\n    Recibos conhecidos usam as rotas diretas complete/finalize. Um recibo em\n    fase anterior sem driver de retomada compatível é preservado e recusado\n    antes de checkpoints; não é convertido em um envio novo. Nessas rotas\n    diretas, um horário histórico ausente é conservado sem gerar outro clock.\n\n    Retorna a lista de sidecars atualizados (estado final de cada tentativa).\n    '
    return _delivery_recovery.pump_pending(_recovery_services(), session, state, max_retries, retry_backoff, account_email, required_org_key, session_ids, **kwargs)



def _media_services() -> _delivery_media.MediaServices:
    return _delivery_media.MediaServices(
        BACKLOG_CAP_MS=BACKLOG_CAP_MS,
        Path=Path,
        UploadError=UploadError,
        _http_upload_error=_http_upload_error,
        _normalize_recorded_at_sequence=_normalize_recorded_at_sequence,
        _probe_duration_ms=_probe_duration_ms,
        _run_ffmpeg=_run_ffmpeg,
        _status_from_exception=_status_from_exception,
        _video_dims=_video_dims,
        _video_handler=_video_handler,
        _video_pix_fmt=_video_pix_fmt,
        config=config,
        ffmpeg_bin=ffmpeg_bin,
        format_recorded_at=format_recorded_at,
        probe_video=probe_video,
        recorded_at_to_wall_ms=recorded_at_to_wall_ms,
        time=time,
        transport=transport,
        urllib=urllib,
    )


def _protocol_services() -> _delivery_protocol.ProtocolServices:
    return _delivery_protocol.ProtocolServices(
        JsonStateError=JsonStateError,
        STATE_DONE=STATE_DONE,
        UploadError=UploadError,
        _error_detail=_error_detail,
        _header=_header,
        _http_upload_error=_http_upload_error,
        _require_completion_flags=_require_completion_flags,
        _session_request=_session_request,
        _validate_known_receipt=_validate_known_receipt,
        _validate_native_upload_out=_validate_native_upload_out,
        decode_json_state=decode_json_state,
        get_upload=get_upload,
        json=json,
        random=random,
        re=re,
        recorded_at_to_wall_ms=recorded_at_to_wall_ms,
        time=time,
    )


def _journal_services() -> _delivery_journal.JournalServices:
    return _delivery_journal.JournalServices(
        JsonStateError=JsonStateError,
        Path=Path,
        STATE_FAILED=STATE_FAILED,
        STATE_RETRY_LATE=STATE_RETRY_LATE,
        STATE_TRANSPORT=STATE_TRANSPORT,
        UploadError=UploadError,
        _CURL_ERROR_CODE=_CURL_ERROR_CODE,
        _CURL_TRANSPORT_RETRY_CODES=_CURL_TRANSPORT_RETRY_CODES,
        _csv_span_ns=_csv_span_ns,
        _iso_now=_iso_now,
        _journal_flag=_journal_flag,
        _journal_recorded_at=_journal_recorded_at,
        _load_sidecar_from_path=_load_sidecar_from_path,
        _read_sidecar_file=_read_sidecar_file,
        _response_upload_id=_response_upload_id,
        _sidecar_archive_path=_sidecar_archive_path,
        _sidecar_filename=_sidecar_filename,
        _sidecar_path=_sidecar_path,
        _sidecar_resume_payload=_sidecar_resume_payload,
        _validate_journal_owner=_validate_journal_owner,
        _validate_sidecar_zip=_validate_sidecar_zip,
        _verify_video_content_digest=_verify_video_content_digest,
        decode_json_state=decode_json_state,
        hashlib=hashlib,
        io=io,
        is_pending_transport=is_pending_transport,
        journal_flags_valid=journal_flags_valid,
        json=json,
        load_json_state=load_json_state,
        re=re,
        save_json=save_json,
        sidecars_dir=sidecars_dir,
        urllib=urllib,
        zipfile=zipfile,
    )


def _chunk_services() -> _delivery_chunk.ChunkServices:
    return _delivery_chunk.ChunkServices(
        CaptureImportError=CaptureImportError,
        ChunkResult=ChunkResult,
        JsonStateError=JsonStateError,
        Path=Path,
        STATE_COMPLETING=STATE_COMPLETING,
        STATE_CREATING=STATE_CREATING,
        STATE_DONE=STATE_DONE,
        STATE_FAILED=STATE_FAILED,
        STATE_LOSS=STATE_LOSS,
        STATE_QUARANTINE=STATE_QUARANTINE,
        STATE_RETRY_LATE=STATE_RETRY_LATE,
        STATE_TRANSPORT=STATE_TRANSPORT,
        UploadError=UploadError,
        _conflict_upload=_conflict_upload,
        _existing_receipt_error=_existing_receipt_error,
        _http_upload_error=_http_upload_error,
        _journal_flag=_journal_flag,
        _probe_duration_ms=_probe_duration_ms,
        _put_blob=_put_blob,
        _put_blob_file=_put_blob_file,
        _require_completion_flags=_require_completion_flags,
        _response_upload_id=_response_upload_id,
        _session_request=_session_request,
        _sidecar_resume_payload=_sidecar_resume_payload,
        _status_from_exception=_status_from_exception,
        _upload_owner=_upload_owner,
        _validate_journal_owner=_validate_journal_owner,
        _validate_native_upload_out=_validate_native_upload_out,
        _validate_sidecar_zip=_validate_sidecar_zip,
        _validated_sas_urls=_validated_sas_urls,
        _verify_video_content_digest=_verify_video_content_digest,
        _video_resume_payload=_video_resume_payload,
        _with_retry=_with_retry,
        build_metadata_json=build_metadata_json,
        build_sidecar_zip=build_sidecar_zip,
        complete_upload=complete_upload,
        config=config,
        decode_json_state=decode_json_state,
        default_device_meta=default_device_meta,
        default_platform_meta=default_platform_meta,
        fail_upload=fail_upload,
        hashlib=hashlib,
        inspect_original_capture=inspect_original_capture,
        is_pending_transport=is_pending_transport,
        probe_video=probe_video,
        re=re,
        recorded_at_to_wall_ms=recorded_at_to_wall_ms,
    )


def _session_services() -> _delivery_session.SessionServices:
    return _delivery_session.SessionServices(
        CaptureDescriptor=CaptureDescriptor,
        CaptureImportError=CaptureImportError,
        Path=Path,
        STATE_COMPLETING=STATE_COMPLETING,
        STATE_CREATING=STATE_CREATING,
        STATE_DONE=STATE_DONE,
        STATE_FAILED=STATE_FAILED,
        STATE_QUARANTINE=STATE_QUARANTINE,
        UploadError=UploadError,
        UploadResult=UploadResult,
        _chunk_sidecar=_chunk_sidecar,
        _existing_receipt_error=_existing_receipt_error,
        _finalize_session=_finalize_session,
        _iso_now=_iso_now,
        _journal_creation_uncertain=_journal_creation_uncertain,
        _journal_flag=_journal_flag,
        _journal_has_remote_receipt=_journal_has_remote_receipt,
        _journal_recorded_at=_journal_recorded_at,
        _normalize_recorded_at_sequence=_normalize_recorded_at_sequence,
        _original_group_payloads=_original_group_payloads,
        _probe_duration_ms=_probe_duration_ms,
        _recorded_at_sequence_from_base=_recorded_at_sequence_from_base,
        _remove_sidecar_archive=_remove_sidecar_archive,
        _require_completion_flags=_require_completion_flags,
        _sidecar_archive_path=_sidecar_archive_path,
        _uncertain_creation_error=_uncertain_creation_error,
        _upload_owner=_upload_owner,
        _upload_single_chunk=_upload_single_chunk,
        _validate_journal_owner=_validate_journal_owner,
        _verify_video_content_digest=_verify_video_content_digest,
        _with_retry=_with_retry,
        default_device_meta=default_device_meta,
        default_network_meta=default_network_meta,
        default_platform_meta=default_platform_meta,
        default_video_meta=default_video_meta,
        delete_session=delete_session,
        delete_upload=delete_upload,
        evaluate_upload=evaluate_upload,
        fail_upload=fail_upload,
        format_recorded_at=format_recorded_at,
        hashlib=hashlib,
        is_perfect=is_perfect,
        journal_flags_valid=journal_flags_valid,
        load_sidecar=load_sidecar,
        normalize_video=normalize_video,
        probe_video=probe_video,
        re=re,
        recorded_at_to_wall_ms=recorded_at_to_wall_ms,
        recording_start_epoch=recording_start_epoch,
        save_bytes=save_bytes,
        save_sidecar=save_sidecar,
        summarize_checks=summarize_checks,
        upload_session=upload_session,
        uuid=uuid,
    )


def _recovery_services() -> _delivery_recovery.RecoveryServices:
    return _delivery_recovery.RecoveryServices(
        MAX_CRASH_RESUMES=MAX_CRASH_RESUMES,
        Path=Path,
        STATE_COMPLETING=STATE_COMPLETING,
        STATE_DONE=STATE_DONE,
        STATE_FAILED=STATE_FAILED,
        STATE_LOSS=STATE_LOSS,
        STATE_QUARANTINE=STATE_QUARANTINE,
        STATE_RETRY_LATE=STATE_RETRY_LATE,
        TRANSIENT_STATES=TRANSIENT_STATES,
        UploadError=UploadError,
        _existing_receipt_error=_existing_receipt_error,
        _finalize_session=_finalize_session,
        _iso_now=_iso_now,
        _journal_creation_uncertain=_journal_creation_uncertain,
        _journal_flag=_journal_flag,
        _journal_has_remote_receipt=_journal_has_remote_receipt,
        _journal_recorded_at=_journal_recorded_at,
        _pending_recovery_stage=_pending_recovery_stage,
        _remove_sidecar_archive=_remove_sidecar_archive,
        _session_resource_identity=_session_resource_identity,
        _sidecar_filename=_sidecar_filename,
        _sidecar_resume_payload=_sidecar_resume_payload,
        _uncertain_creation_error=_uncertain_creation_error,
        _upload_single_chunk=_upload_single_chunk,
        _video_resume_payload=_video_resume_payload,
        _with_retry=_with_retry,
        complete_upload=complete_upload,
        config=config,
        evaluate_upload=evaluate_upload,
        get_upload=get_upload,
        is_pending_evaluation=is_pending_evaluation,
        is_pending_finalization=is_pending_finalization,
        is_pending_transport=is_pending_transport,
        is_perfect=is_perfect,
        is_remote_recovery_candidate=is_remote_recovery_candidate,
        is_terminal_remote_failure=is_terminal_remote_failure,
        journal_delivery_confirmed=journal_delivery_confirmed,
        journal_flags_valid=journal_flags_valid,
        list_sidecars=list_sidecars,
        load_sidecar=load_sidecar,
        mark_remote_terminal_failure=mark_remote_terminal_failure,
        save_sidecar=save_sidecar,
        upload_session=upload_session,
        validate_remote_recovery_receipt=validate_remote_recovery_receipt,
    )
