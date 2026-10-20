"""Parallel account workers share one measured read, without stale caching."""
from concurrent.futures import Future, ThreadPoolExecutor
import threading
from unittest.mock import patch

from moneymin import sidecar


def test_three_workers_share_one_read_and_receive_independent_lists(tmp_path):
    video = tmp_path / "fixture.mp4"
    video.write_bytes(b"inert fixture")
    entered, release, joined = threading.Event(), threading.Event(), threading.Event()
    waiter_lock = threading.Lock()
    waiters = 0

    class ObservedFuture(Future):
        def result(self, *args, **kwargs):
            nonlocal waiters
            with waiter_lock:
                waiters += 1
                if waiters == 2:
                    joined.set()
            return super().result(*args, **kwargs)

    def read(_path):
        entered.set()
        assert release.wait(5)
        return [(0, True), (33_333_000, False)]

    with patch.object(sidecar, "Future", ObservedFuture), \
         patch.object(sidecar, "_read_frame_pts", side_effect=read) as probe, \
         ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(sidecar._extract_frame_pts, video)
        assert entered.wait(5)
        others = [pool.submit(sidecar._extract_frame_pts, video) for _ in range(2)]
        try:
            assert joined.wait(5)
        finally:
            release.set()
        results = [future.result() for future in (first, *others)]
        assert all(row == [(0, True), (33_333_000, False)] for row in results)
        assert probe.call_count == 1
        results[0].clear()
        assert len(results[1]) == len(results[2]) == 2
        sidecar._extract_frame_pts(video)
        assert probe.call_count == 2  # No persistent timestamp cache.
    assert not sidecar._FRAME_READS


def test_changed_file_during_read_never_publishes_obsolete_timestamps(tmp_path):
    video = tmp_path / "fixture.mp4"
    video.write_bytes(b"before")

    def read(_path):
        video.write_bytes(b"changed file content")
        return [(0, True)]

    with patch.object(sidecar, "_read_frame_pts", side_effect=read):
        assert sidecar._extract_frame_pts(video) == []
    assert not sidecar._FRAME_READS


def test_failed_read_is_not_cached_and_later_read_can_succeed(tmp_path):
    video = tmp_path / "fixture.mp4"
    video.write_bytes(b"fixture")
    with patch.object(sidecar, "_read_frame_pts", side_effect=[[], [(0, True)]]):
        assert sidecar._extract_frame_pts(video) == []
        assert sidecar._extract_frame_pts(video) == [(0, True)]
    assert not sidecar._FRAME_READS
