"""
campaign.py — Orquestração end-to-end de datasets egocêntricos -> Minute.

Responsável por, dado um conjunto de clipes + contas + tasks:
  1. Preparar cada clipe (baixar vídeo + IMU real, normalizar, montar sidecar).
  2. Enviar para cada conta configurada (upload_session com IMU real).
  3. Gerar um relatório estruturado (JSON) com os resultados e status.

Fluxo completo (contrato wire Minute 1.29 / SM-S901E):
  Ego4D(clipe+IMU real) -> normalize_video(1440x1080 yuv420p) -> sidecar nativo
  -> upload_session(register_first, evaluate, finalize) -> relatório.

Regras de ouro (não descumprir):
  - O cenário do vídeo DEVE corresponder à task do Minute (`task` score).
  - IMU real do Ego4D (não sintética) no sidecar; grade 500 Hz no domínio
    ``android_elapsedRealtimeNanos`` (mesmo eixo de frames/timebase).
  - Gate de entrega: envelope 1.29 válido + proveniência honesta third_party.
  - Vídeos longos: `timeout_blob` alto para o PUT do blob não estourar.
"""
from __future__ import annotations

import datetime
import gzip
import hashlib
import json
import math
import os
import random
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import (
    FIRST_COMPLETED,
    Future,
    ThreadPoolExecutor,
    wait,
)
from contextlib import contextmanager
from functools import lru_cache, partial
from pathlib import Path
from typing import Any

from . import (
    config,
    device_profile,
    ego4d,
    holoassist,
    nymeria,
    org_policy,
    recording_timeline,
    sent_registry,
    task_matching,
)
from .campaign_types import (
    DEFAULT_ACCOUNT_STAGGER_S,
    DEFAULT_TIMEOUT_BLOB,
    MIN_ACCOUNT_AGE_DAYS,
    AccountSpec,
    CampaignConfig,
    CampaignLog,
    TaskSpec,
)
from .campaigns import admission as _campaign_admission
from .campaigns import preparation as _campaign_preparation
from .campaigns.workers import ClipPrefetch as _ClipPrefetchWorker
from .integrations import provider_ids_for, selection_ids, supports_selection
from .device_profile import DeviceProfile
from .content_selection import diverse_order, diversity_summary, parent_key
from .minute_api import AuthError, Session, validate_task_catalog
from .media_lifecycle import cleanup_operation
from .campaign_state import campaign_state_operation
from .campaign_history import is_campaign_history_name
from .sidecar import (
    build_frames_csv,
    build_frames_csv_from_video,
    build_imu_csv,
    build_sidecar_zip_custom,
    ffmpeg_bin,
    probe_video,
)
from .task_catalog import (
    BOOSTED_TASKS,
    CATEGORY_PT,
    SCENARIO_PT,
    TASK_NAME_PT,
    TASK_TO_SCENARIO,
)

from .campaigns import reconciliation as _campaign_reconciliation


def _campaign_reconciliation_context():
    return SimpleNamespace(
        Path=Path,
        UploadError=UploadError,
        _acknowledge_campaign_upload=_acknowledge_campaign_upload,
        _legacy_upload_contexts=_legacy_upload_contexts,
        all_terminal_remote_failure=all_terminal_remote_failure,
        config=config,
        is_campaign_history_name=is_campaign_history_name,
        journal_delivery_confirmed=journal_delivery_confirmed,
        json=json,
        list_sidecars=list_sidecars,
        re=re,
        save_sidecar=save_sidecar,
        sent_registry=sent_registry,
    )


from .campaigns import lifecycle as _campaign_lifecycle


def _campaign_lifecycle_context():
    return SimpleNamespace(
        Path=Path,
        _NATIVE_CACHE_VERSION=_NATIVE_CACHE_VERSION,
        _cleanup_rejected_prepare=_cleanup_rejected_prepare,
        _cleanup_uploaded_item=_cleanup_uploaded_item,
        _delete_media_files=_delete_media_files,
        _expected_prefetch_paths=_expected_prefetch_paths,
        _media_companions=_media_companions,
        _native_cache_fingerprint=_native_cache_fingerprint,
        _within=_within,
        config=config,
        holoassist=holoassist,
        journal_delivery_confirmed=journal_delivery_confirmed,
        json=json,
        os=os,
        re=re,
        _account_video_ok_path=_account_video_ok_path,
        _lock_for_account_video=_lock_for_account_video,
    )


from .campaigns import preparation_engine as _campaign_preparation_engine


def _campaign_preparation_context():
    return SimpleNamespace(
        ACCOUNT_ENCODE_WORKERS=ACCOUNT_ENCODE_WORKERS,
        MAX_DUR_MS=MAX_DUR_MS,
        MIN_DUR_MS=MIN_DUR_MS,
        NATIVE_BITRATE=NATIVE_BITRATE,
        Path=Path,
        UploadError=UploadError,
        _ACCOUNT_ENCODE_RESERVATION_LOCK=_ACCOUNT_ENCODE_RESERVATION_LOCK,
        _ACCOUNT_ENCODE_SLOTS=_ACCOUNT_ENCODE_SLOTS,
        _ACCOUNT_VIDEO_ENCODE_VERSION=_ACCOUNT_VIDEO_ENCODE_VERSION,
        _ACCOUNT_VIDEO_LOCKS=_ACCOUNT_VIDEO_LOCKS,
        _CHUNK_VIDEO_LOCKS_GUARD=_CHUNK_VIDEO_LOCKS_GUARD,
        _NATIVE_CACHE_LOCKS=_NATIVE_CACHE_LOCKS,
        _NATIVE_CACHE_LOCKS_GUARD=_NATIVE_CACHE_LOCKS_GUARD,
        _NATIVE_CACHE_VERSION=_NATIVE_CACHE_VERSION,
        _account_video_locks=_account_video_locks,
        _account_video_ok_path=_account_video_ok_path,
        _account_video_path=_account_video_path,
        _account_video_tmp_path=_account_video_tmp_path,
        _chunk_video_lock=_chunk_video_lock,
        _chunk_video_locks=_chunk_video_locks,
        _chunk_video_signature=_chunk_video_signature,
        _cpu_slots=_cpu_slots,
        _ego_clip_inputs=_ego_clip_inputs,
        _ego_prepare_plan=_ego_prepare_plan,
        _ffmpeg_bin=_ffmpeg_bin,
        _ffmpeg_encoder_available=_ffmpeg_encoder_available,
        _ffmpeg_head=_ffmpeg_head,
        _ffmpeg_run=_ffmpeg_run,
        _frames_csv=_frames_csv,
        _heavy_encode_threads=_heavy_encode_threads,
        _lock_for_account_video=_lock_for_account_video,
        _measured_packet_tail_args=_measured_packet_tail_args,
        _measured_pts_container_args=_measured_pts_container_args,
        _measured_video_pts=_measured_video_pts,
        _native_cache_backup=_native_cache_backup,
        _native_cache_duration_ok=_native_cache_duration_ok,
        _native_cache_fingerprint=_native_cache_fingerprint,
        _native_cache_guard=_native_cache_guard,
        _native_cache_key=_native_cache_key,
        _native_cache_marker=_native_cache_marker,
        _native_cache_marker_matches=_native_cache_marker_matches,
        _native_cache_temp=_native_cache_temp,
        _native_container_args=_native_container_args,
        _native_video_codec_args=_native_video_codec_args,
        _normalize_ego_clip_for_prepare=_normalize_ego_clip_for_prepare,
        _normalize_video=_normalize_video,
        _record_generated_media=_record_generated_media,
        _require_same_video_pts=_require_same_video_pts,
        _use_nvenc=_use_nvenc,
        _valid_account_video=_valid_account_video,
        build_frames_csv_from_video=build_frames_csv_from_video,
        build_sidecar_zip_custom=build_sidecar_zip_custom,
        config=config,
        device_profile=device_profile,
        ego4d=ego4d,
        ffmpeg_bin=ffmpeg_bin,
        hashlib=hashlib,
        holoassist=holoassist,
        json=json,
        math=math,
        nymeria=nymeria,
        os=os,
        prepare_clip=prepare_clip,
        probe_video=probe_video,
        random=random,
        threading=threading,
        time=time,
        uuid=uuid,
    )



from types import SimpleNamespace
from .campaigns import catalog as _campaign_catalog


def _campaign_catalog_context():
    return SimpleNamespace(
        Path=Path,
        _NATIVE_CACHE_VERSION=_NATIVE_CACHE_VERSION,
        _RANK_LOCK=_RANK_LOCK,
        _clip_is_cached=_clip_is_cached,
        _clip_window=_clip_window,
        _duration_ranked_pools=_duration_ranked_pools,
        _duration_ranked_snapshot=_duration_ranked_snapshot,
        _duration_window_candidates=_duration_window_candidates,
        _ego_prepare_plan=_ego_prepare_plan,
        _link_rank_history=_link_rank_history,
        _load_rank_cache=_load_rank_cache,
        _load_rank_seed=_load_rank_seed,
        _local_narration_catalog=_local_narration_catalog,
        _merge_rank_seed=_merge_rank_seed,
        _narration_file_present=_narration_file_present,
        _nymeria_windows=_nymeria_windows,
        _prepare_queue_accepts=_prepare_queue_accepts,
        _rank_cache_path=_rank_cache_path,
        _rank_cache_stamp=_rank_cache_stamp,
        _rank_evidenced=_rank_evidenced,
        _rank_seed_bytes_cached=_rank_seed_bytes_cached,
        _rank_seed_path=_rank_seed_path,
        _ranked_pools=_ranked_pools,
        _ranked_pools_cached=_ranked_pools_cached,
        _ranked_pools_snapshot=_ranked_pools_snapshot,
        _refresh_rank_inputs=_refresh_rank_inputs,
        _same_action_already_covered=_same_action_already_covered,
        _save_rank_cache=_save_rank_cache,
        _stamp_hides_present_narration=_stamp_hides_present_narration,
        _task_candidates=_task_candidates,
        _union_ranked_clips=_union_ranked_clips,
        config=config,
        ego4d=ego4d,
        ego_clip_cache_state=ego_clip_cache_state,
        gzip=gzip,
        hashlib=hashlib,
        holoassist=holoassist,
        json=json,
        math=math,
        normalize_dataset_provider=normalize_dataset_provider,
        nymeria=nymeria,
        re=re,
        task_matching=task_matching,
        uuid=uuid,
        _candidate_audio_possible=_candidate_audio_possible,
        _catalog_clip_cached_hint=_catalog_clip_cached_hint,
        _catalog_queue_accepts=_catalog_queue_accepts,
        _compatible_task_clips=_compatible_task_clips,
        _with_cached_expansion=_with_cached_expansion,
        normalize_content_mode=normalize_content_mode,
        provider_ids_for=provider_ids_for,
        BOOSTED_TASKS=BOOSTED_TASKS,
        CATEGORY_PT=CATEGORY_PT,
        Session=Session,
        TASK_NAME_PT=TASK_NAME_PT,
        _bind_session_email=_bind_session_email,
        account_registration_route=account_registration_route,
        diversity_summary=diversity_summary,
        validate_task_catalog=validate_task_catalog,
    )

from .upload import (
    UploadError, all_terminal_remote_failure,
    pump_pending, upload_session, list_sidecars, save_sidecar,
    is_pending_transport, is_remote_recovery_candidate,
)
from .upload_types import (
    STATE_COMPLETING, STATE_RETRY_LATE, journal_delivery_confirmed,
    is_pending_evaluation, is_pending_finalization,
)

from .campaigns import account_delivery as _campaign_account_delivery

from .campaigns import imu_rejections

from .campaigns import workers as _campaign_workers


def _campaign_workers_context():
    return SimpleNamespace(
        ACCOUNT_ENCODE_WORKERS=ACCOUNT_ENCODE_WORKERS,
        ThreadPoolExecutor=ThreadPoolExecutor,
        _per_account_video=_per_account_video,
        device_profile=device_profile,
        sent_registry=sent_registry,
    )


def _campaign_account_delivery_context():
    return SimpleNamespace(
        AuthError=AuthError,
        MAX_DUR_MS=MAX_DUR_MS,
        MIN_DUR_MS=MIN_DUR_MS,
        Path=Path,
        Session=Session,
        UploadError=UploadError,
        _AccountRouteError=_AccountRouteError,
        _bind_session_email=_bind_session_email,
        _build_sidecar=_build_sidecar,
        _chunk_plan=_chunk_plan,
        _chunk_video_path=_chunk_video_path,
        _cut_video_chunk=_cut_video_chunk,
        _measured_video_pts=_measured_video_pts,
        _new_identity=_new_identity,
        _nymeria_resample_delivery=_nymeria_resample_delivery,
        _per_account_video=_per_account_video,
        _pump_account_pending=_pump_account_pending,
        _reconcile_uploads=_reconcile_uploads,
        _slice_imu_csv=_slice_imu_csv,
        _terminal_retry_parent=_terminal_retry_parent,
        account_registration_route=account_registration_route,
        build_frames_csv_from_video=build_frames_csv_from_video,
        build_imu_csv=build_imu_csv,
        config=config,
        device_profile=device_profile,
        ego4d=ego4d,
        list_sidecars=list_sidecars,
        nymeria=nymeria,
        org_policy=org_policy,
        probe_video=probe_video,
        time=time,
        upload_session=upload_session,
    )

_SESSION_TYPE = Session

__all__ = [
    "AccountSpec",
    "BOOSTED_TASKS",
    "CampaignConfig",
    "CampaignLog",
    "CATEGORY_PT",
    "DATASET_PROVIDERS",
    "DEFAULT_ACCOUNT_STAGGER_S",
    "DEFAULT_TIMEOUT_BLOB",
    "MIN_ACCOUNT_AGE_DAYS",
    "SCENARIO_PT",
    "TASK_NAME_PT",
    "TASK_TO_SCENARIO",
    "TaskSpec",
    "available_tasks",
    "cleanup_media_cache",
    "list_campaign_logs",
    "normalize_dataset_provider",
    "prepare_clip",
    "prepare_holoassist_clip",
    "prepare_nymeria_clip",
    "run_campaign",
    "session_result",
    "upload_to_account",
    "warm_task_catalog",
]

# Resolução/bitrate da normalização local; validação usa os quadros efetivamente codificados.
NATIVE_WIDTH, NATIVE_HEIGHT = 1440, 1080
NATIVE_BITRATE = "8000k"
# Ryzen 9600X: 12 threads lógicas. Seis encodes × 2 threads ocupam a CPU sem
# deixar cada processo x264 tentar monopolizar todos os núcleos.
ACCOUNT_ENCODE_WORKERS = max(1, min(6, (os.cpu_count() or 2) // 2))
_ACCOUNT_ENCODE_SLOTS = threading.BoundedSemaphore(ACCOUNT_ENCODE_WORKERS)
# Serializa apenas a aquisição de reservas. Sem isso, dois prepares podem
# segurar metade dos slots cada um e esperar eternamente pela outra metade.
# A execução e a liberação permanecem concorrentes.
_ACCOUNT_ENCODE_RESERVATION_LOCK = threading.Lock()
# Teto de PUTs simultâneos. O vídeo-base já está pronto antes dos PUTs e o
# hardware desta estação comporta seis conexões sem disparar todas as contas
# de uma vez. Todas as contas entram no lote; só N voam em cada onda.
DEFAULT_MAX_ACCOUNT_WORKERS = 6

from .campaigns import execution as _campaign_execution


def _campaign_execution_context():
    return SimpleNamespace(
        CampaignLog=CampaignLog,
        FIRST_COMPLETED=FIRST_COMPLETED,
        Path=Path,
        STATE_COMPLETING=STATE_COMPLETING,
        STATE_RETRY_LATE=STATE_RETRY_LATE,
        ThreadPoolExecutor=ThreadPoolExecutor,
        _account_video_path=_account_video_path,
        _acknowledge_campaign_upload=_acknowledge_campaign_upload,
        _all_pending_uploads_succeeded=_all_pending_uploads_succeeded,
        _batch_footage_overlaps=_batch_footage_overlaps,
        _candidate_reserved_emails=_candidate_reserved_emails,
        _candidate_sent_emails=_candidate_sent_emails,
        _cleanup_confirmed_account_media=_cleanup_confirmed_account_media,
        _cleanup_rejected_prepare=_cleanup_rejected_prepare,
        _cleanup_stopped_prepared_media=_cleanup_stopped_prepared_media,
        _cleanup_uploaded_item=_cleanup_uploaded_item,
        _clip_is_cached=_clip_is_cached,
        _compatible_task_clips=_compatible_task_clips,
        _ego_clip_inputs=_ego_clip_inputs,
        _enforce_account_video_cache=_enforce_account_video_cache,
        _interruptible_sleep=_interruptible_sleep,
        _is_disabled_error=_is_disabled_error,
        _prefer_cached_clips=_prefer_cached_clips,
        _prefetch_following=_prefetch_following,
        _pump_account_pending=_pump_account_pending,
        _reconcile_uploads=_reconcile_uploads,
        _recorded_at_now=_recorded_at_now,
        _tick_every=_tick_every,
        _try_prepare_imu_carve=_try_prepare_imu_carve,
        _wait_for_window=_wait_for_window,
        _warm_account_videos=_warm_account_videos,
        _window_remaining_s=_window_remaining_s,
        all_terminal_remote_failure=all_terminal_remote_failure,
        automatic_candidates=automatic_candidates,
        clamp_account_workers=clamp_account_workers,
        device_profile=device_profile,
        diverse_order=diverse_order,
        diversity_summary=diversity_summary,
        ego4d=ego4d,
        holoassist=holoassist,
        imu_rejections=imu_rejections,
        is_pending_evaluation=is_pending_evaluation,
        is_pending_finalization=is_pending_finalization,
        is_pending_transport=is_pending_transport,
        is_remote_recovery_candidate=is_remote_recovery_candidate,
        list_sidecars=list_sidecars,
        math=math,
        max_account_workers=max_account_workers,
        normalize_content_mode=normalize_content_mode,
        normalize_dataset_provider=normalize_dataset_provider,
        nymeria=nymeria,
        parent_key=parent_key,
        partial=partial,
        prepare_clip=prepare_clip,
        prepare_holoassist_clip=prepare_holoassist_clip,
        prepare_nymeria_clip=prepare_nymeria_clip,
        random=random,
        recording_timeline=recording_timeline,
        sent_registry=sent_registry,
        task_matching=task_matching,
        time=time,
        upload_to_account=upload_to_account,
        wait=wait,
    )




class _AccountRouteError(AuthError):
    """Safe account-route failure; never includes proxy host credentials."""

    def __init__(self, phase: str):
        self.route_phase = phase
        super().__init__("Não foi possível usar a rota de conexão atribuída à conta.",
                         code="network")


@contextmanager
def account_registration_route(email: str) -> Iterator[None]:
    """Use an account's assigned proxy for one bounded remote operation."""
    from . import registration_proxy

    try:
        route = registration_proxy.account_route(email)
        route.__enter__()
    except Exception as exc:
        raise _AccountRouteError(getattr(exc, "phase", "setup")) from None
    primary_error = None
    try:
        yield
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            route.__exit__(None, None, None)
        except Exception as exc:
            if primary_error is None:
                raise _AccountRouteError(getattr(exc, "phase", "cleanup")) from None


def _bind_session_email(session: Any, email: str) -> None:
    """Reject a real cached Session owned by another normalized identity."""
    if isinstance(session, _SESSION_TYPE):
        session.with_email(email)


def _pump_account_pending(session: Any, account_email: str, required_org_key: str,
                          **kwargs: Any) -> list[dict[str, Any]]:
    with account_registration_route(account_email):
        return pump_pending(session, account_email=account_email,
                            required_org_key=required_org_key, **kwargs)


def max_account_workers() -> int:
    """Teto de uploads simultâneos. Env MINUTE_MAX_ACCOUNT_WORKERS sobrescreve."""
    raw = os.environ.get("MINUTE_MAX_ACCOUNT_WORKERS", "").strip()
    if raw:
        try:
            return max(1, min(15, int(raw)))
        except ValueError:
            pass
    return DEFAULT_MAX_ACCOUNT_WORKERS


def clamp_account_workers(requested: int, n_accounts: int) -> int:
    """Quantas contas voam ao mesmo tempo: pedido, tamanho do lote e teto."""
    n = max(1, int(n_accounts or 1))
    want = max(1, int(requested or 1))
    return max(1, min(want, n, max_account_workers()))


def _is_disabled_error(error: str | None) -> bool:
    text = (error or "").lower()
    # Só a resposta explícita conhecida confirma desativação; palavras soltas não.
    return bool(re.search(r"\(403\):\s*user account is disabled\.(?:\s|$)", text))
# Janela de duração aceita (recording-config: min 60s / max 1800s).
MIN_DUR_MS, MAX_DUR_MS = 60000, 1800000
# Cada vídeo selecionado deve permanecer um único envio sempre que couber no
# limite devolvido pelo recording-config. Dividir nominalmente a cada 8 min
# fazia uma gravação humana de 15 min aparecer no parceiro como dois envios.
# O sufixo `{session}_{i}` continua existindo apenas para gravações que
# realmente excedam o teto remoto.
DATASET_PROVIDERS = frozenset(selection_ids())
CONTENT_MODES = frozenset({"both", "cache", "dataset"})


# --- meios (helpers) ----------------------------------------------------------

def normalize_dataset_provider(value: str | None) -> str:
    provider = str(value or "all").strip().lower()
    if not supports_selection(provider):
        raise ValueError("dataset inválido (all|ambos|ego4d|holoassist|nymeria)")
    return provider


def normalize_content_mode(value: str | None) -> str:
    mode = str(value or "both").strip().lower()
    if mode not in CONTENT_MODES:
        raise ValueError("modo de conteúdo inválido (both|cache|dataset)")
    return mode

def _frames_csv(duration_ms: int, fps: float = 30.0) -> str:
    return build_frames_csv(duration_ms, fps)


def _recorded_at_now() -> str:
    return device_profile.format_recorded_at(time.time())


def _ffmpeg_head(ff: str) -> list[str]:
    return _campaign_preparation_engine._ffmpeg_head(_campaign_preparation_context(), ff)


def _ffmpeg_run(cmd: list[str], timeout: int=3600):
    return _campaign_preparation_engine._ffmpeg_run(_campaign_preparation_context(), cmd, timeout)


def _heavy_encode_threads() -> int:
    return _campaign_preparation_engine._heavy_encode_threads(_campaign_preparation_context())


@lru_cache(maxsize=4)
def _ffmpeg_encoder_available(ff: str, encoder: str) -> bool:
    return _campaign_preparation_engine._ffmpeg_encoder_available(_campaign_preparation_context(), ff, encoder)


def _use_nvenc(ff: str) -> bool:
    return _campaign_preparation_engine._use_nvenc(_campaign_preparation_context(), ff)


def _native_video_codec_args(*, nvenc: bool, bitrate: str | None=None, gop: int | None=None) -> list[str]:
    return _campaign_preparation_engine._native_video_codec_args(_campaign_preparation_context(), nvenc=nvenc, bitrate=bitrate, gop=gop)


def _native_container_args(tmp: Path) -> list[str]:
    return _campaign_preparation_engine._native_container_args(_campaign_preparation_context(), tmp)


def _measured_pts_container_args(tmp: Path) -> list[str]:
    return _campaign_preparation_engine._measured_pts_container_args(_campaign_preparation_context(), tmp)


def _measured_video_pts(path: Path) -> list[int]:
    return _campaign_preparation_engine._measured_video_pts(_campaign_preparation_context(), path)


def _require_same_video_pts(source: Path, output: Path, *, start_ns: int=0, end_ns: int | None=None) -> int:
    return _campaign_preparation_engine._require_same_video_pts(_campaign_preparation_context(), source, output, start_ns=start_ns, end_ns=end_ns)


def _measured_packet_tail_args(source: Path, *, start_ns: int=0, end_ns: int | None=None) -> list[str]:
    return _campaign_preparation_engine._measured_packet_tail_args(_campaign_preparation_context(), source, start_ns=start_ns, end_ns=end_ns)


@contextmanager
def _cpu_slots(n: int) -> Iterator[None]:
    return _campaign_preparation_engine._cpu_slots(_campaign_preparation_context(), n)


def _normalize_video(src: Path, out_dir: Path, *, start_s: float | None=None, dur_s: float | None=None, stem: str | None=None) -> Path:
    return _campaign_preparation_engine._normalize_video(_campaign_preparation_context(), src, out_dir, start_s=start_s, dur_s=dur_s, stem=stem)

def _ffmpeg_bin() -> str:
    return _campaign_preparation_engine._ffmpeg_bin(_campaign_preparation_context())


# --- preparação de um clipe ---------------------------------------------------

def _interruptible_sleep(
    delay_s: float,
    should_stop: Callable[[], bool] | None,
    emit: Callable[..., None],
    *,
    kind: str = "delay_tick",
    tick_every: float = 1.0,
) -> bool:
    """Espera `delay_s` em fatias de 0.5s, checando `should_stop()` e emitindo
    ticks (`kind`, a cada `tick_every` s) com o countdown para a UI.
    Devolve True se foi interrompida."""
    remaining = float(delay_s)
    next_tick = remaining  # emite já no primeiro passo
    while remaining > 0:
        if should_stop and should_stop():
            return True
        if remaining <= next_tick:
            emit(kind, remaining_s=int(remaining + 0.999))
            next_tick = remaining - tick_every
        step = min(0.5, remaining)
        time.sleep(step)
        remaining -= step
    return should_stop() if should_stop else False


def _tick_every(total_s: float) -> float:
    """Granularidade do countdown: fina em esperas curtas, grossa em longas."""
    return 1.0 if total_s <= 120 else 5.0


def _window_remaining_s(active_hours: tuple[int, int], now=None) -> float:
    """Segundos até a próxima abertura da janela (0 se já está dentro dela).

    Janela em hora local, início <= hora < fim — ex.: (7, 18) = das 7h às 18h.
    """
    start_h, end_h = active_hours
    now = now or datetime.datetime.now()
    mins = now.hour * 60 + now.minute + now.second / 60
    start_m, end_m = start_h * 60, end_h * 60
    if start_m <= mins < end_m:
        return 0.0
    delta = start_m - mins if mins < start_m else (24 * 60 - mins) + start_m
    return delta * 60


def _wait_for_window(
    active_hours: tuple[int, int],
    should_stop: Callable[[], bool] | None,
    emit: Callable[..., None],
) -> bool:
    """Aguarda a janela de envio abrir (ticks de 60s). True se interrompida."""
    first = True
    while True:
        rem = _window_remaining_s(active_hours)
        if rem <= 0:
            return False
        if first:
            emit("window_wait_start", active_hours=list(active_hours),
                 remaining_s=int(rem))
            first = False
        if _interruptible_sleep(rem, should_stop, emit,
                                kind="window_wait_tick", tick_every=60.0):
            return True

def _ego_clip_inputs(clip_info: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    return _campaign_preparation_engine._ego_clip_inputs(_campaign_preparation_context(), clip_info)


# v4 stat-only markers cannot prove source/output bytes. They are not reused;
# failed migration preserves the old pair, successful preparation publishes v5.
_NATIVE_CACHE_VERSION = 5
_NATIVE_CACHE_LOCKS: dict[str, Any] = {}
_NATIVE_CACHE_LOCKS_GUARD = threading.Lock()


@contextmanager
def _native_cache_guard(native: Path) -> Iterator[None]:
    return _campaign_preparation_engine._native_cache_guard(_campaign_preparation_context(), native)


def _native_cache_fingerprint(path: Path, *, allow_empty: bool=False) -> tuple[int, str]:
    return _campaign_preparation_engine._native_cache_fingerprint(_campaign_preparation_context(), path, allow_empty=allow_empty)


def _record_generated_media(path: Path, *, root: Path, role: str) -> None:
    return _campaign_preparation_engine._record_generated_media(_campaign_preparation_context(), path, root=root, role=role)


def _native_cache_key(src: Path, start_s: float | None, dur_s: float | None) -> dict[str, Any]:
    return _campaign_preparation_engine._native_cache_key(_campaign_preparation_context(), src, start_s, dur_s)


def _native_cache_marker(src: Path, native: Path, start_s: float | None, dur_s: float | None) -> dict[str, Any]:
    return _campaign_preparation_engine._native_cache_marker(_campaign_preparation_context(), src, native, start_s, dur_s)


def _native_cache_marker_matches(saved: Any, src: Path, native: Path, start_s: float | None, dur_s: float | None) -> bool:
    return _campaign_preparation_engine._native_cache_marker_matches(_campaign_preparation_context(), saved, src, native, start_s, dur_s)


def _native_cache_duration_ok(probe: dict[str, Any], dur_s: float | None) -> bool:
    return _campaign_preparation_engine._native_cache_duration_ok(_campaign_preparation_context(), probe, dur_s)


def _native_cache_temp(path: Path, suffix: str) -> Path:
    return _campaign_preparation_engine._native_cache_temp(_campaign_preparation_context(), path, suffix)


def _native_cache_backup(path: Path) -> Path | None:
    return _campaign_preparation_engine._native_cache_backup(_campaign_preparation_context(), path)

def _ego_prepare_plan(clip: dict[str, Any]) -> dict[str, Any]:
    """Janela e caminhos que `prepare_clip` grava em disco.

    O acelerador usa este plano para reconhecer um cache já pronto sem
    reencode. Qualquer mudança de corte tem de passar por aqui.
    """
    return _campaign_preparation.ego_prepare_plan(clip, ego4d.clip_window_s)


def ego_clip_cache_state(clip_info: dict[str, Any], work_dir: Path) -> str:
    return _campaign_preparation_engine.ego_clip_cache_state(_campaign_preparation_context(), clip_info, work_dir)


def _try_prepare_imu_carve(clip_info: dict[str, Any], work_dir: Path, *, min_dur_s: float, max_dur_s: float, allow_download: bool, progress: Callable[[str, dict[str, Any]], None] | None, log: Callable[[str], None], emit: Callable[..., None], display_name: str) -> dict[str, Any] | None:
    return _campaign_preparation_engine._try_prepare_imu_carve(_campaign_preparation_context(), clip_info, work_dir, min_dur_s=min_dur_s, max_dur_s=max_dur_s, allow_download=allow_download, progress=progress, log=log, emit=emit, display_name=display_name)


def _normalize_ego_clip_for_prepare(clip: dict[str, Any]) -> dict[str, Any]:
    """Ensure list_clips / carved / ranked records all satisfy ``_ego_prepare_plan``."""
    return _campaign_preparation.normalize_ego_clip(clip)


def prepare_clip(clip: dict[str, Any], video: dict[str, Any], work_dir: Path, *, progress: Callable[[str, dict[str, Any]], None] | None=None, allow_download: bool=True, should_stop: Callable[[], bool] | None=None) -> dict[str, Any]:
    return _campaign_preparation_engine.prepare_clip(_campaign_preparation_context(), clip, video, work_dir, progress=progress, allow_download=allow_download, should_stop=should_stop)


def prepare_nymeria_clip(clip: dict[str, Any], work_dir: Path, *, progress: Callable[[str, dict[str, Any]], None] | None=None, allow_download: bool=True, should_stop: Callable[[], bool] | None=None) -> dict[str, Any]:
    return _campaign_preparation_engine.prepare_nymeria_clip(_campaign_preparation_context(), clip, work_dir, progress=progress, allow_download=allow_download, should_stop=should_stop)


def prepare_holoassist_clip(clip: dict[str, Any], work_dir: Path, *, progress: Callable[[str, dict[str, Any]], None] | None=None, allow_download: bool=True, should_stop: Callable[[], bool] | None=None) -> dict[str, Any]:
    return _campaign_preparation_engine.prepare_holoassist_clip(_campaign_preparation_context(), clip, work_dir, progress=progress, allow_download=allow_download, should_stop=should_stop)


def _new_identity(duration_s: float, email: str, recorded_at: str | None=None, *, limits: dict[str, int] | None=None) -> tuple[str, str, str]:
    return _campaign_preparation_engine._new_identity(_campaign_preparation_context(), duration_s, email, recorded_at, limits=limits)


def _chunk_plan(duration_ms: int, target_ms: int | None=None, limits: dict[str, int] | None=None) -> list[tuple[int, int]]:
    return _campaign_preparation_engine._chunk_plan(_campaign_preparation_context(), duration_ms, target_ms, limits)


def _slice_imu_csv(imu_csv: str, start_ms: int, duration_ms: int) -> tuple[str, int]:
    return _campaign_preparation_engine._slice_imu_csv(_campaign_preparation_context(), imu_csv, start_ms, duration_ms)


_CHUNK_VIDEO_LOCKS_GUARD = threading.Lock()
_chunk_video_locks: dict[str, threading.Lock] = {}


def _chunk_video_signature(src: Path, start_s: float, dur_s: float, *, preserve_pts: bool=False) -> str:
    return _campaign_preparation_engine._chunk_video_signature(_campaign_preparation_context(), src, start_s, dur_s, preserve_pts=preserve_pts)


def _chunk_video_path(src: Path, chunk_index: int, start_ms: int, duration_ms: int, *, preserve_pts: bool=False) -> Path:
    return _campaign_preparation_engine._chunk_video_path(_campaign_preparation_context(), src, chunk_index, start_ms, duration_ms, preserve_pts=preserve_pts)


def _chunk_video_lock(dest: Path) -> threading.Lock:
    return _campaign_preparation_engine._chunk_video_lock(_campaign_preparation_context(), dest)


def _cut_video_chunk(src: Path, dest: Path, start_s: float, dur_s: float, *, preserve_pts: bool=False) -> Path:
    return _campaign_preparation_engine._cut_video_chunk(_campaign_preparation_context(), src, dest, start_s, dur_s, preserve_pts=preserve_pts)


def _build_sidecar(item: dict[str, Any], session_id: str, log_id: str, recorded_at: str, profile: DeviceProfile, video_probe: dict[str, Any], imu_csv: str, frames_csv: str, *, chunk_index: int=0, duration_ms: int | None=None) -> bytes:
    return _campaign_preparation_engine._build_sidecar(_campaign_preparation_context(), item, session_id, log_id, recorded_at, profile, video_probe, imu_csv, frames_csv, chunk_index=chunk_index, duration_ms=duration_ms)


# Invalida cache `_acc*.mp4` gerado antes do envelope Android (handler
# VideoHandler, High@4.2, áudio estéreo 256k) / com B-frames.
_ACCOUNT_VIDEO_ENCODE_VERSION = "4"

_ACCOUNT_VIDEO_LOCKS = threading.Lock()
_account_video_locks: dict[str, threading.Lock] = {}


def _account_video_path(base: Path, profile: DeviceProfile) -> Path:
    return _campaign_preparation_engine._account_video_path(_campaign_preparation_context(), base, profile)


def _lock_for_account_video(path: Path) -> threading.Lock:
    return _campaign_preparation_engine._lock_for_account_video(_campaign_preparation_context(), path)


def _account_video_ok_path(out: Path) -> Path:
    return _campaign_preparation_engine._account_video_ok_path(_campaign_preparation_context(), out)


def _account_video_tmp_path(out: Path) -> Path:
    return _campaign_preparation_engine._account_video_tmp_path(_campaign_preparation_context(), out)


def _valid_account_video(out: Path, base: Path, *, preserve_pts: bool=False) -> bool:
    return _campaign_preparation_engine._valid_account_video(_campaign_preparation_context(), out, base, preserve_pts=preserve_pts)


def _per_account_video(base: Path, profile: DeviceProfile, *, preserve_pts: bool=False) -> Path:
    return _campaign_preparation_engine._per_account_video(_campaign_preparation_context(), base, profile, preserve_pts=preserve_pts)


class _ClipPrefetch(_ClipPrefetchWorker):
    """Facade adapter supplying patchable campaign hooks to the worker."""
    def __init__(self, work_dir: Path, *, should_stop: Callable[[], bool] | None = None) -> None:
        super().__init__(work_dir, should_stop=should_stop,
            expected_paths=lambda: _expected_prefetch_paths,
            prepare_holoassist=lambda: prepare_holoassist_clip,
            prepare_nymeria=lambda: prepare_nymeria_clip,
            ego_clip_inputs=lambda: _ego_clip_inputs,
            prepare_ego=lambda: prepare_clip)


def _prefetch_following(prep: _ClipPrefetch, clips: list[dict[str, Any]], current_uid: str, emails: list[str], registry_key: str) -> None:
    return _campaign_workers._prefetch_following(_campaign_workers_context(), prep, clips, current_uid, emails, registry_key)


def _warm_account_videos(base: Path, emails: list[str]) -> ThreadPoolExecutor | None:
    return _campaign_workers._warm_account_videos(_campaign_workers_context(), base, emails)


@cleanup_operation
def _enforce_account_video_cache(work_dir: Path) -> tuple[int, int]:
    return _campaign_lifecycle._enforce_account_video_cache(_campaign_lifecycle_context(), work_dir)


def _within(path: Path, root: Path) -> bool:
    return _campaign_lifecycle._within(_campaign_lifecycle_context(), path, root)


@cleanup_operation
def _delete_media_files(paths: list[Path], *, allowed_roots: tuple[Path, ...], protection: dict[str, Any] | None=None) -> dict[str, Any]:
    return _campaign_lifecycle._delete_media_files(_campaign_lifecycle_context(), paths, allowed_roots=allowed_roots, protection=protection)


def _media_companions(path: Path) -> list[Path]:
    return _campaign_lifecycle._media_companions(_campaign_lifecycle_context(), path)


def _expected_prefetch_paths(clip_info: dict[str, Any], work_dir: Path) -> list[Path]:
    return _campaign_lifecycle._expected_prefetch_paths(_campaign_lifecycle_context(), clip_info, work_dir)


def _cleanup_uploaded_item(item: dict[str, Any], work_dir: Path, *, protected_paths: set[Path] | None=None) -> dict[str, Any]:
    return _campaign_lifecycle._cleanup_uploaded_item(_campaign_lifecycle_context(), item, work_dir, protected_paths=protected_paths)


def _cleanup_rejected_prepare(clip: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    return _campaign_lifecycle._cleanup_rejected_prepare(_campaign_lifecycle_context(), clip, work_dir)


def _cleanup_stopped_prepared_media(item: dict[str, Any] | None, clip: dict[str, Any] | None, work_dir: Path, prefetch: _ClipPrefetch, *, cleanup_candidate: bool=True) -> list[dict[str, Any]]:
    return _campaign_lifecycle._cleanup_stopped_prepared_media(_campaign_lifecycle_context(), item, clip, work_dir, prefetch, cleanup_candidate=cleanup_candidate)


@cleanup_operation
def cleanup_media_cache(work_dir: Path | None=None, *, provider: str='all') -> dict[str, Any]:
    return _campaign_lifecycle.cleanup_media_cache(_campaign_lifecycle_context(), work_dir, provider=provider)


def _all_pending_uploads_succeeded(pending_accounts: list[AccountSpec], results: dict[str, dict[str, Any]]) -> bool:
    return _campaign_lifecycle._all_pending_uploads_succeeded(_campaign_lifecycle_context(), pending_accounts, results)


def _cleanup_confirmed_account_media(item: dict[str, Any], account: AccountSpec, result: dict[str, Any], paths: list[str], work_dir: Path, *, protected_paths: set[Path]) -> dict[str, Any] | None:
    return _campaign_lifecycle._cleanup_confirmed_account_media(_campaign_lifecycle_context(), item, account, result, paths, work_dir, protected_paths=protected_paths)


# --- envio para uma conta -----------------------------------------------------

def _acknowledge_campaign_upload(session_id: str, rows: list[dict[str, Any]] | None=None) -> None:
    return _campaign_reconciliation._acknowledge_campaign_upload(_campaign_reconciliation_context(), session_id, rows)


def _legacy_upload_context(session_id: str, email: str) -> dict[str, Any] | None:
    return _campaign_reconciliation._legacy_upload_context(_campaign_reconciliation_context(), session_id, email)


def _legacy_upload_contexts(wanted: set[tuple[str, str]], journals: list[dict] | None=None) -> dict:
    return _campaign_reconciliation._legacy_upload_contexts(_campaign_reconciliation_context(), wanted, journals)


def _terminal_retry_parent(rows: list[dict[str, Any]], account: AccountSpec, item: dict[str, Any], task_id: str) -> str | None:
    return _campaign_reconciliation._terminal_retry_parent(_campaign_reconciliation_context(), rows, account, item, task_id)


def _reconcile_uploads(rows: list[dict[str, Any]], account: AccountSpec, item: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    return _campaign_reconciliation._reconcile_uploads(_campaign_reconciliation_context(), rows, account, item, task_id)


def _nymeria_resample_delivery(item: dict[str, Any], *, origin_ns: int | None=None, duration_ms: int | None=None) -> tuple[str, dict[str, Any]]:
    return _campaign_reconciliation._nymeria_resample_delivery(_campaign_reconciliation_context(), item, origin_ns=origin_ns, duration_ms=duration_ms)


@campaign_state_operation
def upload_to_account(item: dict[str, Any], account: AccountSpec, task_id: str, timeout_blob: int, evaluate: bool, finalize: bool, on_progress: Callable[..., None] | None=None, unique_video: bool=False, session: Session | None=None, session_cache: dict[str, Session] | None=None, recorded_at: str | None=None, recover_pending: bool=True, **_legacy: Any) -> dict[str, Any]:
    return _campaign_account_delivery.upload_to_account(_campaign_account_delivery_context(), item, account, task_id, timeout_blob, evaluate, finalize, on_progress, unique_video, session, session_cache, recorded_at, recover_pending, **_legacy)


# --- orquestração -------------------------------------------------------------

def list_campaign_logs(data_dir: Path | None = None) -> list[Path]:
    """Campaign logs salvos (`data/campaign_*.json`), mais recentes primeiro.

    Usado pela interface web (aba Histórico) para listar campanhas anteriores.
    Ignora `campaign.example.json` (não é um log de execução).
    """
    data_dir = Path(data_dir) if data_dir else config.DATA_DIR
    paths: list[Path] = []
    if data_dir.exists():
        for p in data_dir.iterdir():
            if is_campaign_history_name(p.name):
                paths.append(p)
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return paths


@campaign_state_operation
def run_campaign(
    config: CampaignConfig,
    log: CampaignLog | None = None,
    *,
    progress: Callable[[str, dict[str, Any]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> CampaignLog:
    """Executa e persiste o ciclo completo, inclusive em falhas inesperadas."""
    if config.original_capture_plan is not None:
        from .original_capture import run_original_capture_campaign
        return run_original_capture_campaign(config, log, progress=progress, should_stop=should_stop)
    emails = [account.email.strip().casefold() for account in config.accounts]
    if not emails or any(not email for email in emails) or len(set(emails)) != len(emails):
        raise ValueError("Selecione contas válidas, sem duplicatas.")
    if not config.tasks:
        raise ValueError("Selecione ao menos uma categoria.")
    log = log or CampaignLog(started_at=_recorded_at_now(),
                             accounts=[a.email for a in config.accounts])
    log.start_request_id = config.start_request_id
    log.save()
    prefetch = _ClipPrefetch(Path(config.work_dir), should_stop=should_stop)
    try:
        return _run_campaign(config, log, progress=progress,
                             should_stop=should_stop, prefetch=prefetch)
    except Exception as exc:
        # The runner can restore confirmed progress after an observer fails.
        # Keep the original exception/type and immutable attempt; this private
        # reference is never inferred from server JSON or exposed in the API.
        try:
            exc._moneymin_campaign_log = log
        except Exception:
            # A custom exception may forbid private attributes. Recording
            # progress is best effort and must preserve the original failure.
            pass
        try:
            with log._save_lock:
                log.status = "error"
                log.issues.append({"kind": "campaign_error", "error": f"{type(exc).__name__}: {exc}"})
                log.save()
        except OSError as save_error:
            exc.add_note(f"Não foi possível salvar a falha da campanha: {save_error}")
        raise
    finally:
        prefetch.shutdown()


@ego4d.selection_boundary
def automatic_candidates(tsk: TaskSpec, config: CampaignConfig, *, catalog_only: bool=False) -> list[dict[str, Any]]:
    return _campaign_catalog.automatic_candidates(_campaign_catalog_context(), tsk, config, catalog_only=catalog_only)


def _candidate_audio_possible(clip: dict[str, Any]) -> bool:
    return _campaign_admission.audio_possible(clip)


def _candidate_sent_emails(registry_key: str, clip: dict) -> set[str]:
    return _campaign_admission.sent_emails(registry_key, clip, sent_registry.sent_emails)


def _candidate_reserved_emails(config: CampaignConfig, clip: dict) -> set[str]:
    return _campaign_admission.reserved_emails(config, clip)


def _batch_footage_overlaps(previous: dict, candidate: dict) -> bool:
    return _campaign_admission.footage_overlaps(
        previous, candidate, clip_window=_clip_window,
        same_action=_same_action_already_covered)


def _run_campaign(config: CampaignConfig, log: CampaignLog | None=None, *, progress: Callable[[str, dict[str, Any]], None] | None=None, should_stop: Callable[[], bool] | None=None, prefetch: _ClipPrefetch) -> CampaignLog:
    return _campaign_execution._run_campaign(_campaign_execution_context(), config, log, progress=progress, should_stop=should_stop, prefetch=prefetch)


def session_result(
    email: str,
    org_key: str,
    session_id: str,
    *,
    session: Session | None = None,
) -> dict[str, Any]:
    """Consulta o estado de uma sessão (preview + quality scores) numa conta."""
    _bind_session_email(session, email)
    with account_registration_route(email):
        sess = session or Session.from_email(email)
        _bind_session_email(sess, email)
        http_status, body = sess.get(
            f"/api/v1/organizations/{org_key}/sessions/{session_id}")
    import json as _json
    if http_status != 200:
        raise RuntimeError(
            f"Minute devolveu HTTP {http_status} ao consultar a sessão")
    d = _json.loads(body) if isinstance(body, str) else body
    if not isinstance(d, dict):
        raise RuntimeError("Minute devolveu um estado de sessão ilegível")
    files = d.get("files") or []
    uf = d.get("unprocessedFiles") or []
    if not isinstance(files, list) or not isinstance(uf, list):
        raise RuntimeError("Minute devolveu uma lista de arquivos ilegível")
    status = "processing"
    quality = None
    if files and not uf:
        quality = files[0].get("quality") if isinstance(files[0], dict) else None
        status = "preview_ready"
    elif uf:
        states = [
            str(item.get("previewStatus") or "pending").strip().lower()
            if isinstance(item, dict) else "pending"
            for item in uf
        ]
        if any(value == "unavailable" for value in states):
            status = "unprocessed:unavailable"
        elif any(value not in {"pending", "processing"} for value in states):
            status = "unprocessed:" + next(
                value for value in states
                if value not in {"pending", "processing"})
        else:
            status = "processing"
    preview_states = [
        str(item.get("previewStatus") or "pending").strip().lower()
        if isinstance(item, dict) else "pending"
        for item in uf
    ]
    return {
        "session_id": session_id, "email": email, "status": status,
        "quality": quality, "task": d.get("taskName"),
        "ready_files": len(files),
        "pending_files": sum(
            value in {"pending", "processing"} for value in preview_states),
        "unavailable_files": sum(
            value == "unavailable" for value in preview_states),
        "total_files": len(files) + len(uf),
    }


@lru_cache(maxsize=1)
def _task_candidates() -> tuple[dict[str, Any], ...]:
    return _campaign_catalog._task_candidates(_campaign_catalog_context())


_RANK_LOCK = threading.Lock()


def _rank_cache_path() -> Path:
    return _campaign_catalog._rank_cache_path(_campaign_catalog_context())


def _rank_seed_path() -> Path:
    return _campaign_catalog._rank_seed_path(_campaign_catalog_context())


@lru_cache(maxsize=1)
def _rank_seed_bytes_cached(raw: bytes | None) -> dict[str, tuple[dict[str, Any], ...]] | None:
    return _campaign_catalog._rank_seed_bytes_cached(_campaign_catalog_context(), raw)


def _load_rank_seed() -> dict[str, tuple[dict[str, Any], ...]] | None:
    return _campaign_catalog._load_rank_seed(_campaign_catalog_context())


_load_rank_seed.cache_clear = _rank_seed_bytes_cached.cache_clear


@ego4d.selection_boundary
def _rank_cache_stamp() -> tuple[tuple[str, int, str], ...]:
    return _campaign_catalog._rank_cache_stamp(_campaign_catalog_context())


def _rank_stamp_clear() -> None:
    return _campaign_catalog._rank_stamp_clear(_campaign_catalog_context())


_rank_cache_stamp.cache_clear = _rank_stamp_clear


@ego4d.selection_boundary
def _rank_evidenced(buckets):
    return _campaign_catalog._rank_evidenced(_campaign_catalog_context(), buckets)


def _clip_window(clip: dict[str, Any]) -> tuple[float, float] | None:
    return _campaign_catalog._clip_window(_campaign_catalog_context(), clip)


def _same_action_already_covered(kept: list[dict[str, Any]], clip: dict[str, Any]) -> bool:
    return _campaign_catalog._same_action_already_covered(_campaign_catalog_context(), kept, clip)


def _union_ranked_clips(spans: dict[str, list[dict[str, Any]] | tuple[dict[str, Any], ...]], official: dict[str, list[dict[str, Any]] | tuple[dict[str, Any], ...]], *, min_dur_s: float=60, max_dur_s: float=1800) -> dict[str, tuple[dict[str, Any], ...]]:
    return _campaign_catalog._union_ranked_clips(_campaign_catalog_context(), spans, official, min_dur_s=min_dur_s, max_dur_s=max_dur_s)


def _link_rank_history(current: dict[str, tuple[dict[str, Any], ...]], previous: dict[str, tuple[dict[str, Any], ...]]) -> dict[str, tuple[dict[str, Any], ...]]:
    return _campaign_catalog._link_rank_history(_campaign_catalog_context(), current, previous)


def _narration_file_present(path: Path) -> bool:
    return _campaign_catalog._narration_file_present(_campaign_catalog_context(), path)


def _local_narration_catalog() -> bool:
    return _campaign_catalog._local_narration_catalog(_campaign_catalog_context())


def _stamp_hides_present_narration(stamp) -> bool:
    return _campaign_catalog._stamp_hides_present_narration(_campaign_catalog_context(), stamp)


def _prepare_queue_accepts(clip: dict[str, Any], task_name: str | None, *, fresh: bool=True) -> bool:
    return _campaign_catalog._prepare_queue_accepts(_campaign_catalog_context(), clip, task_name, fresh=fresh)


def _merge_rank_seed(buckets: dict[str, tuple[dict[str, Any], ...]], *, min_dur_s: float=60, max_dur_s: float=1800) -> dict[str, tuple[dict[str, Any], ...]]:
    return _campaign_catalog._merge_rank_seed(_campaign_catalog_context(), buckets, min_dur_s=min_dur_s, max_dur_s=max_dur_s)


@ego4d.selection_boundary
def _load_rank_cache(path: Path | None=None) -> dict[str, tuple[dict[str, Any], ...]] | None:
    return _campaign_catalog._load_rank_cache(_campaign_catalog_context(), path)


@ego4d.selection_boundary
def _save_rank_cache(buckets: dict[str, tuple[dict[str, Any], ...]], path: Path | None=None, *, narration_scan: bool | None=None) -> None:
    return _campaign_catalog._save_rank_cache(_campaign_catalog_context(), buckets, path, narration_scan=narration_scan)


@lru_cache(maxsize=1)
def _ranked_pools_snapshot(stamp) -> dict[str, tuple[dict[str, Any], ...]]:
    return _campaign_catalog._ranked_pools_snapshot(_campaign_catalog_context(), stamp)


@ego4d.selection_boundary
def _ranked_pools_cached() -> dict[str, tuple[dict[str, Any], ...]]:
    return _campaign_catalog._ranked_pools_cached(_campaign_catalog_context())


_ranked_pools_cached.cache_clear = _ranked_pools_snapshot.cache_clear


_RANK_INPUT_SIGNATURE = None


def _refresh_rank_inputs() -> None:
    """Called under _RANK_LOCK; new metadata must replace in-memory rankings."""
    global _RANK_INPUT_SIGNATURE
    paths = (
        config.MEDIA_DATA_DIR / "ego4d" / "ego4d.json",
        config.MEDIA_DATA_DIR / "ego4d" / "clips.csv",
        config.MEDIA_DATA_DIR / "ego4d" / "clip_narrations.json",
        ego4d.timed_narrations_path(), _rank_seed_path(),
    )
    signature = []
    for path in paths:
        source = ego4d._selection_source(path)
        signature.append((str(path), source[1], source[2]))
    signature = tuple(signature)
    if signature == _RANK_INPUT_SIGNATURE:
        return
    _task_candidates.cache_clear()
    _rank_cache_stamp.cache_clear()
    _load_rank_seed.cache_clear()
    _ranked_pools_cached.cache_clear()
    _duration_ranked_pools.cache_clear()
    _RANK_INPUT_SIGNATURE = signature


@ego4d.selection_boundary
def _ranked_pools() -> dict[str, tuple[dict[str, Any], ...]]:
    """Todas as tasks de uma vez. Cache em disco para o GET /api/tasks não congelar a UI."""
    with _RANK_LOCK:
        from .catalog_policy import prepared_only_requested
        if not prepared_only_requested():
            ego4d.ensure_task_annotations()
        _refresh_rank_inputs()
        return _ranked_pools_cached()


@lru_cache(maxsize=8)
def _duration_ranked_snapshot(stamp, min_dur_s: float, max_dur_s: float):
    return _campaign_catalog._duration_ranked_snapshot(_campaign_catalog_context(), stamp, min_dur_s, max_dur_s)


def _duration_window_candidates(buckets) -> dict[str, list[dict[str, Any]]]:
    return _campaign_catalog._duration_window_candidates(_campaign_catalog_context(), buckets)


@ego4d.selection_boundary
def _duration_ranked_pools(min_dur_s: float, max_dur_s: float):
    return _campaign_catalog._duration_ranked_pools(_campaign_catalog_context(), min_dur_s, max_dur_s)


_duration_ranked_pools.cache_clear = _duration_ranked_snapshot.cache_clear


def _nymeria_windows(task_name: str, min_dur_s: float, max_dur_s: float, *, catalog_only: bool=False) -> tuple[dict[str, Any], ...]:
    return _campaign_catalog._nymeria_windows(_campaign_catalog_context(), task_name, min_dur_s, max_dur_s, catalog_only=catalog_only)


_nymeria_windows.cache_clear = nymeria.clear_caches


def _compatible_task_clips(task_name: str, dataset_provider: str='all', *, min_dur_s: float=60, max_dur_s: float=1800, catalog_only: bool=False) -> tuple[dict[str, Any], ...]:
    return _campaign_catalog._compatible_task_clips(_campaign_catalog_context(), task_name, dataset_provider, min_dur_s=min_dur_s, max_dur_s=max_dur_s, catalog_only=catalog_only)


def _with_cached_expansion(clips: list[dict[str, Any]], task_name: str, *, min_dur_s: float, max_dur_s: float, work_dir: Path | None=None, include_disabled: bool=False, catalog_only: bool=False) -> list[dict[str, Any]]:
    return _campaign_catalog._with_cached_expansion(_campaign_catalog_context(), clips, task_name, min_dur_s=min_dur_s, max_dur_s=max_dur_s, work_dir=work_dir, include_disabled=include_disabled, catalog_only=catalog_only)


def _clip_is_cached(clip: dict[str, Any], work_dir: Path) -> bool:
    return _campaign_catalog._clip_is_cached(_campaign_catalog_context(), clip, work_dir)


def _catalog_clip_cached_hint(clip: dict[str, Any], work_dir: Path) -> bool:
    return _campaign_catalog._catalog_clip_cached_hint(_campaign_catalog_context(), clip, work_dir)


def _catalog_queue_accepts(clip: dict[str, Any], task_name: str) -> bool:
    return _campaign_catalog._catalog_queue_accepts(_campaign_catalog_context(), clip, task_name)


def _prefer_cached_clips(clips: list[dict[str, Any]], work_dir: Path, *, prioritize: bool=True) -> list[dict[str, Any]]:
    return _campaign_catalog._prefer_cached_clips(_campaign_catalog_context(), clips, work_dir, prioritize=prioritize)


@campaign_state_operation
def warm_task_catalog() -> None:
    """Load the prepared catalog before the first category request."""
    _ranked_pools()


@ego4d.selection_boundary
def available_tasks(email: str, org_key: str, *, min_dur_s: float=60, max_dur_s: float=1800, include_unavailable: bool=False, dataset_provider: str='all', content_mode: str='both', session: Session | None=None, remote_tasks: list[dict[str, Any]] | None=None) -> list[dict[str, Any]]:
    return _campaign_catalog.available_tasks(_campaign_catalog_context(), email, org_key, min_dur_s=min_dur_s, max_dur_s=max_dur_s, include_unavailable=include_unavailable, dataset_provider=dataset_provider, content_mode=content_mode, session=session, remote_tasks=remote_tasks)
