"""Official annotation ingestion remains bounded, atomic and separate from media."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from moneymin import ego4d_annotations as annotations
from moneymin.web import server


class Ego4DAnnotationsTests(unittest.TestCase):
    def test_streaming_schema_reads_wearer_and_other_events_without_inventing_timestamps(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, target = Path(temporary) / "official.json", Path(temporary) / "index.jsonl"
            source.write_text(json.dumps({"parent": {"narration_pass_1": {"narrations": [
                {"timestamp_sec": 1.25, "narration_text": "#C C sweeps the floor"},
                {"timestamp_sec": 2, "narration_text": "#O person washes dishes"},
                {"timestamp_sec": 3, "narration_text": "#summary C cleans"},
                {"timestamp_sec": True, "narration_text": "invalid"}]}},
                "no-imu": {"narration_pass_1": {"narrations": []}}}), encoding="utf-8")
            result = annotations.convert_narrations(source, target, {"parent"})
            row = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(row["events"], [[1.25, "#C C sweeps the floor"], [2.0, "#O person washes dishes"]])
            self.assertEqual(result, {"videos": 1, "events": 2, "rejected_events": 1})

    def test_truncation_duplicate_and_extra_content_are_rejected(self):
        for raw in ('{"a": {}', '{"a":{},"a":{}}', '{"a":{}} trailing'):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as temporary:
                source, target = Path(temporary) / "source", Path(temporary) / "target"
                source.write_text(raw, encoding="utf-8")
                with self.assertRaises(ValueError):
                    annotations.convert_narrations(source, target, {"a"})

    def test_failed_conversion_preserves_existing_index_and_removes_only_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "timed_narrations.jsonl"
            target.write_bytes(b"previous valid source")
            meta = root / "ego4d.json"
            meta.write_text(json.dumps({"videos": [{"video_uid": "a", "has_imu": True}]}))
            def download(bucket, key, path):
                self.assertEqual(key, annotations.ANNOTATIONS_KEY)
                path.write_text('{"a": {')
            with patch.object(annotations.ego4d, "sync_meta", return_value=(meta, root / "clips.csv")), \
                 patch.object(annotations.ego4d, "timed_narrations_path", return_value=target), \
                 patch.object(annotations.ego4d, "_download_to", side_effect=download):
                with self.assertRaises(ValueError):
                    annotations.sync_timed_annotations()
            self.assertEqual(target.read_bytes(), b"previous valid source")
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["ego4d.json", "timed_narrations.jsonl"])

    def test_complete_catalog_api_reuses_single_worker_and_shutdown_waits(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def sync(**kwargs):
            calls.append(1)
            started.set()
            release.wait(5)
            return {"videos": 1, "events": 2}
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / "catalog"
            file.write_text("fixture")
            with patch.object(server, "_migrate_legacy_integrations", return_value={"ego4d": {
                    "access_key_id": "fixture-access", "secret_access_key": "fixture-secret"}}), \
                 patch.object(server, "_apply_ego4d"), \
                 patch.object(server.ego4d, "sync_meta", return_value=(file, file)), \
                 patch.object(annotations, "sync_timed_annotations", side_effect=sync):
                client = server.create_app(for_testing=True).test_client()
                try:
                    for _ in range(2):
                        response = client.post("/api/integrations/ego4d/catalog", json={"include_annotations": True})
                        self.assertEqual(response.status_code, 202)
                        self.assertNotIn("fixture-secret", response.get_data(as_text=True))
                    self.assertTrue(started.wait(2))
                    self.assertFalse(client.post("/api/local/shutdown").get_json()["ready"])
                finally:
                    release.set()
                    for _ in range(100):
                        if client.post("/api/local/shutdown").get_json()["ready"]:
                            break
                        time.sleep(0.01)
                self.assertEqual(calls, [1])
                self.assertTrue(client.post("/api/local/shutdown").get_json()["ready"])
