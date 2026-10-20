"""Offline Nymeria provider contracts with temporary annotations and SDK data."""
from __future__ import annotations

import csv
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class NymeriaProviderTests(unittest.TestCase):
    def setUp(self):
        from moneymin import nymeria, nymeria_vrs

        temporary = tempfile.TemporaryDirectory(prefix="nymeria-provider-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sequence = self.root / "offline-gardening"
        self.media = self.sequence / "recording_head" / "data"
        self.media.mkdir(parents=True)
        (self.sequence / "metadata.json").write_text(json.dumps({
            "uid": "offline-gardening", "head_duration_sec": 300}), encoding="utf8")
        for name in ("data.vrs", "motion.vrs"):
            (self.media / name).write_bytes(b"offline SDK fixture; not a VRS recording")
        narration = self.sequence / "narration"
        narration.mkdir()
        with (narration / "atomic_action.csv").open("w", encoding="utf8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "start_time", "end_time", "Describe my atomic actions"])
            writer.writeheader()
            for index in range(41):
                writer.writerow({"start_time": 1000 + index * 5,
                    "end_time": 1005 + index * 5,
                    "Describe my atomic actions":
                        "C pulls weeds from the garden soil by hand while standing."})

        self.origin_ns = 1_000_000_000_000
        self.rgb = tuple(range(self.origin_ns, self.origin_ns + 300_000_000_001, 20_000_000))
        self.imu = tuple(range(self.origin_ns, self.origin_ns + 300_000_000_001, 4_000_000))
        # Substitute only the external SDK boundary. Annotation parsing,
        # timestamp indexing, selection and IMU resampling remain real.
        patches = [
            patch.dict(os.environ, {"NYMERIA_ROOT": str(self.root)}),
            patch.object(nymeria_vrs, "_provider", side_effect=self.provider),
            patch.object(nymeria, "_sdk_signature", return_value=(("offline-sdk", (1,)),)),
            patch.dict(sys.modules, {"projectaria_tools.core.sensor_data":
                SimpleNamespace(TimeDomain=SimpleNamespace(DEVICE_TIME="offline-device-time"))}),
            patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")),
        ]
        for entry in patches:
            entry.start()
            self.addCleanup(entry.stop)
        nymeria.clear_caches()
        self.addCleanup(nymeria.clear_caches)

    def provider(self, path):
        self.assertIn(Path(path), (self.media / "data.vrs", self.media / "motion.vrs"))
        return self

    def get_stream_id_from_label(self, label):
        self.assertIn(label, ("camera-rgb", "imu-right"))
        return label

    def get_metadata(self):
        return SimpleNamespace(device_serial="offline-fixture-head")

    def get_timestamps_ns(self, stream, domain):
        self.assertEqual(domain, "offline-device-time")
        return self.rgb if stream == "camera-rgb" else self.imu

    def get_num_data(self, stream):
        return len(self.rgb if stream == "camera-rgb" else self.imu)

    def get_imu_data_by_index(self, stream, index):
        self.assertEqual(stream, "imu-right")
        timestamp = self.imu[index]
        return SimpleNamespace(capture_timestamp_ns=timestamp,
            accel_msec2=((timestamp - self.origin_ns) / 1e9, 2., 3.),
            gyro_radsec=(4., 5., 6.), accel_valid=True, gyro_valid=True)

    def test_list_sequences_and_windows(self):
        from moneymin import nymeria
        seqs = nymeria.list_sequences()
        self.assertEqual(len(seqs), 1)
        self.assertEqual(Path(seqs[0]["path"]), self.sequence)
        self.assertTrue(seqs[0]["selection_ready"])
        wins = nymeria.list_windows(seqs[0], task_name="Gardening", min_dur_s=60, max_dur_s=60)
        self.assertTrue(wins, "the fixture must exercise nonempty measured windows")
        for window in wins:
            self.assertTrue(window["clip_uid"].startswith("nymeria:"))
            self.assertEqual(window["source"], "nymeria")
            self.assertEqual(window["dur_s"], 60)

    def test_imu_csv_500hz_and_span_pin(self):
        from moneymin import nymeria, nymeria_vrs
        seq = nymeria.list_sequences()[0]
        t0, t1 = nymeria.device_window_for_sequence(
            Path(seq["path"]), start_s=0.0, end_s=2.0)
        self.assertEqual((t0, t1), (self.origin_ns, self.origin_ns + 2_000_000_000))
        samples = nymeria_vrs.read_imu_samples(
            Path(seq["path"]) / "recording_head" / "data" / "motion.vrs",
            t0_ns=t0, t1_ns=t1)
        stats: dict = {}
        csv_text = nymeria_vrs.build_imu_csv_from_samples(samples, stats=stats)
        self.assertTrue(csv_text.startswith("t,ax,ay,az,wx,wy,wz\n"))
        self.assertEqual(stats["maxInterpolationSpanNs"], "25000000")
        self.assertEqual(stats["strategy"], "gyro_anchored_v1")
        self.assertEqual(len(samples), 501)
        self.assertEqual(stats["sampleCount"], 1001)
        rows = list(csv.DictReader(io.StringIO(csv_text)))
        self.assertEqual([int(row["t"]) for row in rows], list(range(0, 2_000_000_001, 2_000_000)))
        self.assertEqual(float(rows[0]["ax"]), 0.)
        self.assertEqual(float(rows[1]["ax"]), 0.002)
        self.assertEqual(float(rows[-1]["ax"]), 2.)

    def test_normalize_dataset_provider_accepts_nymeria(self):
        from moneymin import campaign
        self.assertEqual(campaign.normalize_dataset_provider("nymeria"), "nymeria")
        self.assertEqual(campaign.normalize_dataset_provider("all"), "all")

    def test_automatic_candidates_include_nymeria(self):
        from moneymin import campaign
        from moneymin.campaign_types import CampaignConfig, TaskSpec
        cfg = CampaignConfig(
            accounts=[],
            tasks=[TaskSpec(task_id="t", task_name="Gardening", scenario="Gardening",
                            count=1, min_dur_s=60, max_dur_s=120)],
            work_dir=str(self.root / "work"),
            dataset_provider="nymeria",
            content_mode="dataset",
        )
        cands = campaign.automatic_candidates(cfg.tasks[0], cfg)
        self.assertTrue(cands, "the campaign must receive eligible fixture candidates")
        for candidate in cands:
            self.assertTrue(str(candidate.get("clip_uid", "")).startswith("nymeria:"))
            self.assertEqual(candidate["selection_evidence"]["task"]["name"], "Gardening")
            self.assertEqual(candidate["selection_evidence"]["task"]["id"], "t")
            self.assertEqual(candidate["selection_evidence"]["task"]["registry_key"],
                             "minute|t|Gardening")
            self.assertGreaterEqual(candidate["dur_s"], 60)
            self.assertLessEqual(candidate["dur_s"], 120)


class NymeriaSelectionTests(unittest.TestCase):
    def test_ambos_combines_ego4d_and_nymeria_without_holoassist(self):
        from moneymin import campaign
        campaign._nymeria_windows.cache_clear()
        ego = {"clip_uid": "ego1", "dur_s": 90, "source": "ego4d"}
        nymeria = {"clip_uid": "nymeria:s:0.000:60.000", "dur_s": 60, "source": "nymeria"}
        with patch.object(campaign, "_ranked_pools", return_value={"Gardening": (ego,)}), \
             patch.object(campaign.task_matching, "canonical_task_name", side_effect=lambda name: name), \
             patch.object(campaign.holoassist, "list_clips") as holo, \
             patch.object(campaign.nymeria, "automatic_candidates", return_value=[nymeria]):
            ambos = campaign._compatible_task_clips("Gardening", "ambos")
            only = campaign._compatible_task_clips("Gardening", "nymeria")
        self.assertEqual([clip["source"] for clip in ambos], ["nymeria", "ego4d"])
        self.assertEqual([clip["source"] for clip in only], ["nymeria"])
        holo.assert_not_called()
        self.assertEqual(campaign.normalize_dataset_provider("ambos"), "ambos")


class NymeriaReadinessTests(unittest.TestCase):
    def test_readiness_accepts_nymeria_and_reports_the_library(self):
        from moneymin import readiness
        with patch("moneymin.nymeria.list_sequences", return_value=[{"selection_ready": True}]), \
             patch("moneymin.nymeria_vrs._bootstrap_projectaria", return_value=None):
            result = readiness.campaign_readiness("nymeria")
        self.assertEqual(result["provider"], "nymeria")
        library = next(item for item in result["checks"]
                       if item["name"] == "Biblioteca Nymeria")
        self.assertEqual(library["status"], "ok")
        self.assertIn("1 sequência", library["detail"])

    def test_readiness_blocks_a_missing_nymeria_library(self):
        from moneymin import readiness
        with patch("moneymin.nymeria.list_sequences", return_value=[]), \
             patch("moneymin.nymeria_vrs._bootstrap_projectaria", return_value=None):
            result = readiness.campaign_readiness("nymeria")
        library = next(item for item in result["checks"]
                       if item["name"] == "Biblioteca Nymeria")
        self.assertEqual(library["status"], "error")
        self.assertFalse(result["ready"])


if __name__ == "__main__":
    unittest.main()
