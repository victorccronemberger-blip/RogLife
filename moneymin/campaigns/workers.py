"""Bounded media prefetch worker with injected campaign hooks."""
from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable


class ClipPrefetch:
    """Prepare one following clip while preserving its in-use paths on cancel."""
    def __init__(self, work_dir: Path, *, should_stop: Callable[[], bool] | None = None,
                 expected_paths, prepare_holoassist, prepare_nymeria,
                 ego_clip_inputs, prepare_ego) -> None:
        self.work_dir = work_dir
        self._should_stop = should_stop
        self._expected_paths = expected_paths
        self._prepare_holoassist = prepare_holoassist
        self._prepare_nymeria = prepare_nymeria
        self._ego_clip_inputs = ego_clip_inputs
        self._prepare_ego = prepare_ego
        self._stop: threading.Event | None = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="moneymin-prefetch")
        self._fut: Future[dict[str, Any]] | None = None
        self._uid: str | None = None
        self._clip_info: dict[str, Any] | None = None
        self._guard_paths: set[Path] = set()
        self._retired: list[tuple[Future[dict[str, Any]], set[Path], dict[str, Any] | None]] = []

    def start(self, clip_info: dict[str, Any]) -> None:
        uid = str(clip_info.get("clip_uid") or "")
        if not uid or (self._uid == uid and self._fut is not None):
            return
        self.cancel()
        self._uid = uid
        self._clip_info = dict(clip_info)
        self._guard_paths = set(self._expected_paths()(clip_info, self.work_dir))
        stop = threading.Event()
        self._stop = stop

        def stopped() -> bool:
            return stop.is_set() or bool(self._should_stop and self._should_stop())

        if clip_info.get("source") == "holoassist":
            self._fut = self._pool.submit(
                self._prepare_holoassist(), clip_info, self.work_dir, should_stop=stopped)
            return
        if clip_info.get("source") == "nymeria":
            self._fut = self._pool.submit(
                self._prepare_nymeria(), clip_info, self.work_dir, should_stop=stopped)
            return
        try:
            clip, video = self._ego_clip_inputs()(clip_info)
        except Exception:
            self._clear_current()
            return
        if clip is None or video is None:
            self._clear_current()
            return
        self._fut = self._pool.submit(
            self._prepare_ego(), clip, video, self.work_dir, should_stop=stopped)

    def take(self, uid: str) -> dict[str, Any] | None:
        if self._fut is None or self._uid != uid:
            return None
        fut = self._fut
        self._clear_current()
        try:
            return fut.result()
        except Exception:
            return None

    def _clear_current(self) -> None:
        self._fut = None
        self._uid = None
        self._clip_info = None
        self._guard_paths = set()

    def cancel(self) -> None:
        if self._stop is not None:
            self._stop.set()
            self._stop = None
        if self._fut is not None:
            if not self._fut.cancel():
                self._retired.append((self._fut, set(self._guard_paths), self._clip_info))
            self._clear_current()

    def protected_paths(self) -> set[Path]:
        active = set(self._guard_paths) if self._fut is not None else set()
        for future, paths, _clip in self._retired:
            if not future.done():
                active.update(paths)
        return active

    def cleanup_candidates(self) -> list[tuple[dict[str, Any] | None, set[Path]]]:
        candidates = []
        if self._fut is not None:
            candidates.append((self._clip_info, set(self._guard_paths)))
        candidates.extend((clip, set(paths)) for future, paths, clip in self._retired if not future.cancelled())
        return candidates

    def shutdown(self) -> None:
        self.cancel()
        self._pool.shutdown(wait=True, cancel_futures=True)


def _prefetch_following(_services, 
    prep: _ClipPrefetch,
    clips: list[dict[str, Any]],
    current_uid: str,
    emails: list[str],
    registry_key: str,
) -> None:
    """Agenda o primeiro clipe seguinte que ainda não foi enviado a todos."""
    seen = False
    for candidate in clips:
        uid = candidate["clip_uid"]
        if not seen:
            if uid == current_uid:
                seen = True
            continue
        if _services.sent_registry.is_sent_to_all(registry_key, uid, emails):
            continue
        prep.start(candidate)
        return

def _warm_account_videos(_services, base: Path, emails: list[str]) -> ThreadPoolExecutor | None:
    """Dispara o re-encode por aparelho em fundo (cache hit = no-op).

    Roda durante a espera da janela / o PUT da primeira conta: o upload
    encontra o arquivo pronto ou espera o mesmo lock.
    """
    if not emails or not base.exists():
        return None
    pool = _services.ThreadPoolExecutor(
        max_workers=_services.ACCOUNT_ENCODE_WORKERS,
        thread_name_prefix="moneymin-acc-encode")
    for email in emails:
        pool.submit(_services._per_account_video, base, _services.device_profile.get_profile(email))
    return pool

REQUIRES = ('ACCOUNT_ENCODE_WORKERS', 'ThreadPoolExecutor', '_per_account_video', 'device_profile', 'sent_registry')
