"""Regression tests for audit F03 prefetch cancel and F08 sync preflight drain."""
from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from moneymin import campaign
from moneymin.web.routes import campaign_admission, campaign_execution


class PrefetchCancelF03(unittest.TestCase):
    def _assert_cancel_reaches(self, source: str, prepare_name: str):
        for campaign_stop in (False, True):
            with self.subTest(source=source, campaign_stop=campaign_stop), TemporaryDirectory() as directory:
                started = threading.Event()
                stopped = threading.Event()
                root_stop = threading.Event()

                def prepare(*args, **kwargs):
                    should_stop = kwargs.get("should_stop")
                    self.assertTrue(callable(should_stop))
                    started.set()
                    for _ in range(500):
                        if should_stop():
                            stopped.set()
                            raise RuntimeError("interrompido")
                        time.sleep(0.01)
                    raise AssertionError("Worker did not receive cancellation")

                prefetch = campaign._ClipPrefetch(Path(directory), should_stop=root_stop.is_set)
                with patch.object(campaign, prepare_name, side_effect=prepare), \
                     patch.object(campaign, "_ego_clip_inputs", return_value=({"clip_uid": "ego:x"}, {"video_uid": "v"})):
                    try:
                        clip = {"clip_uid": f"{source}:fixture", "source": source}
                        if source == "ego4d":
                            clip = {"clip_uid": "ego:fixture", "source": "ego4d"}
                        prefetch.start(clip)
                        self.assertTrue(started.wait(2), "prepare did not start")
                        if campaign_stop:
                            root_stop.set()
                        else:
                            prefetch.cancel()
                        self.assertTrue(stopped.wait(2), "prepare did not see stop")
                    finally:
                        prefetch.shutdown()

    def test_holoassist_prefetch_receives_stop(self):
        self._assert_cancel_reaches("holoassist", "prepare_holoassist_clip")

    def test_ego4d_prefetch_receives_stop(self):
        self._assert_cancel_reaches("ego4d", "prepare_clip")


class SyncPreflightDrainF08(unittest.TestCase):
    def test_sync_preflight_counts_in_campaign_drain_while_running(self):
        entered = threading.Event()
        release = threading.Event()
        drain = {"requested": False, "requests": 0}
        lock = threading.RLock()

        def slow_preflight(body, progress=lambda _m: None):
            entered.set()
            if not release.wait(5):
                raise AssertionError("release not signaled")
            return {"ok": True}, 200

        services = MagicMock()
        services.campaign_drain = drain
        services._HEAVY_RUNNER_LOCK = lock
        services.run_campaign_preflight = slow_preflight
        services.request = MagicMock()
        services.request.get_json = MagicMock(return_value={"accounts": ["a@x"], "tasks": [{"task_id": "t"}]})
        services.request.args = {}
        services._validate_campaign_request = MagicMock()
        services.jsonify = lambda result: result
        services.re = __import__("re")
        services.hashlib = __import__("hashlib")
        services.json = __import__("json")

        # Bind unbound route functions the way DependencyBindings would.
        def campaign_preflight():
            return campaign_admission.campaign_preflight(services)

        def background_campaign_preflight(body, progress):
            return campaign_admission.background_campaign_preflight(services, body, progress)

        services.background_campaign_preflight = background_campaign_preflight
        services.run_campaign_preflight = slow_preflight

        worker = threading.Thread(target=campaign_preflight, daemon=True)
        worker.start()
        self.assertTrue(entered.wait(2))
        with lock:
            self.assertEqual(drain["requests"], 1)
            drain["requested"] = True
            ready = (drain["requested"] and drain["requests"] == 0)
        self.assertFalse(ready, "close_drain must wait for sync preflight")
        release.set()
        worker.join(5)
        self.assertFalse(worker.is_alive())
        with lock:
            self.assertEqual(drain["requests"], 0)
            ready = (drain["requested"] and drain["requests"] == 0)
        self.assertTrue(ready)


if __name__ == "__main__":
    unittest.main()
