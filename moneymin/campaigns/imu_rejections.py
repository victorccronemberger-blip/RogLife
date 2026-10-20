"""Persist Ego4D clips skipped because of a real IMU coverage gap.

Distinct from ``sent_registry``: these clips were never delivered — they were
rejected by sensor evidence and would repeat the same wasted download/encode
in every new campaign. The store records the failing minimum duration so a
task with a smaller minimum can still retry the clip. A full reset erases it;
a scoped history reset keeps it, because the sensor truth did not change.
"""
from __future__ import annotations

import math
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import config
from ..atomic_io import JsonStateError, load_json_state, save_json
from ..campaign_state import campaign_state_operation

FILE_NAME = "rejected_imu_videos.json"
_LOCK = threading.RLock()
_REQUIRED_FIELDS = ("min_dur_s", "longest_continuous_s", "detail",
                    "recorded_at", "task_id", "task_name")
_INVALID = ("Registro de vídeos ignorados por IMU inválido ou ilegível; "
            "restaure o arquivo antes de continuar.")


def _path() -> Path:
    """Caminho do store (lido na hora — DATA_DIR é patchável nos testes)."""
    return config.DATA_DIR / FILE_NAME


def _valid_entry(entry: Any) -> bool:
    if not isinstance(entry, dict) or any(field not in entry for field in _REQUIRED_FIELDS):
        return False
    duration, longest = entry.get("min_dur_s"), entry.get("longest_continuous_s")
    return (
        type(duration) in (int, float) and math.isfinite(duration) and duration > 0
        and type(longest) in (int, float) and math.isfinite(longest) and longest >= 0
        and isinstance(entry.get("detail"), str) and bool(entry["detail"])
        and isinstance(entry.get("recorded_at"), str) and bool(entry["recorded_at"])
        and isinstance(entry.get("task_id"), str)
        and isinstance(entry.get("task_name"), str) and bool(entry["task_name"]))


def _identities(clip: dict) -> list[str]:
    uid = clip.get("clip_uid")
    if not isinstance(uid, str) or not uid:
        raise ValueError("Identidade de clipe inválida.")
    return [uid, *(dedup for dedup in (clip.get("dedup_clip_uids") or ())
                    if isinstance(dedup, str) and dedup)]


def _load_locked() -> dict[str, dict[str, Any]]:
    path = _path()
    if not path.exists():
        return {}
    try:
        raw = load_json_state(path, None)
    except JsonStateError:
        raise ValueError(_INVALID) from None
    if not isinstance(raw, dict) or any(
            not isinstance(uid, str) or not uid or not _valid_entry(entry)
            for uid, entry in raw.items()):
        raise ValueError(_INVALID)
    return {uid: entry for uid, entry in raw.items()}


def _save(registry: dict[str, dict[str, Any]]) -> None:
    save_json(_path(), registry)


@campaign_state_operation
def load() -> dict[str, dict[str, Any]]:
    """Carrega o store; ausente devolve {} e corrompido levanta ValueError."""
    with _LOCK:
        return _load_locked()


def blocking_entry(
    registry: dict[str, dict[str, Any]], clip: dict, min_dur_s: float,
) -> dict[str, Any] | None:
    """Primeira lacuna persistida que já bloqueia o clipe nesta duração mínima.

    Uma entrada bloqueia quando sua duração mínima de falha é menor ou igual à
    pedida agora: o vídeo já falhou para um pedido pelo menos tão estrito, logo
    baixar/reprocessar de novo repetiria o mesmo desperdício.
    """
    if type(min_dur_s) not in (int, float) or not math.isfinite(min_dur_s):
        raise ValueError("Duração mínima de IMU inválida.")
    for identity in _identities(clip):
        entry = registry.get(identity)
        if entry is not None and float(entry["min_dur_s"]) <= float(min_dur_s):
            return entry
    return None


def longest_continuous_s(clip: dict, work_dir: Path, *, max_dur_s: float) -> float | None:
    """Mede a maior peça contínua real do CSV local; None quando não verificável."""
    from ..imu_coverage import longest_clippable_seconds
    return longest_clippable_seconds(clip, work_dir, max_s=max_dur_s)


@campaign_state_operation
def record(
    clip: dict, *, min_dur_s: float, longest_continuous_s: float, detail: str,
    task_id: str, task_name: str, recorded_at: str | None = None,
) -> dict[str, Any] | None:
    """Persiste a lacuna real do clipe, mantendo o pedido mais estrito já visto.

    Só grava quando ``longest_continuous_s < min_dur_s`` (pedido impossível por
    lacuna real). Repetir o mesmo mínimo — ou um maior — devolve a entrada
    anterior sem regravar; um mínimo menor atualiza a entrada, pois o pedido
    mais brando também já está bloqueado.
    """
    if (type(min_dur_s) not in (int, float) or not math.isfinite(min_dur_s) or min_dur_s <= 0
            or type(longest_continuous_s) not in (int, float)
            or not math.isfinite(longest_continuous_s) or longest_continuous_s < 0):
        raise ValueError("Medidas de lacuna de IMU inválidas.")
    if longest_continuous_s >= min_dur_s:
        return None
    uid = clip.get("clip_uid")
    if not isinstance(uid, str) or not uid:
        raise ValueError("Identidade de clipe inválida.")
    if not (isinstance(detail, str) and detail
            and isinstance(task_id, str)
            and isinstance(task_name, str) and task_name):
        raise ValueError("Campos de lacuna de IMU inválidos.")
    stamp = recorded_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not isinstance(stamp, str) or not stamp:
        raise ValueError("Campos de lacuna de IMU inválidos.")
    entry = {"min_dur_s": float(min_dur_s), "longest_continuous_s": float(longest_continuous_s),
             "detail": detail, "recorded_at": stamp, "task_id": task_id, "task_name": task_name}
    with _LOCK:
        registry = _load_locked()
        previous = registry.get(uid)
        if (isinstance(previous, dict) and "min_dur_s" in previous
                and float(previous["min_dur_s"]) <= float(min_dur_s)):
            return previous
        registry[uid] = entry
        _save(registry)
        return entry