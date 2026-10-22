"""Local selector regressions: one window identity and evidence-bound epsilon."""
import copy
import json
import math
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from moneymin import config, ego4d, task_matching
from moneymin.campaigns.preparation import normalize_ego_clip


class TemporalCoordinateTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())) / "ego4d"
        self.root.mkdir()
        self.stack.enter_context(patch.object(config, "MEDIA_DATA_DIR", self.root.parent))
        self.task = "Folding Clothes or Putting Them on Hangers"
        self.text = "#C C folds the shirt"
        self.video = {
            "video_uid": "parent", "duration_sec": 900, "has_imu": True,
            "scenarios": ["Cleaning / laundry"],
            "imu_metadata": {"s3_path": "s3://fixture/imu.csv", "component_metadata": [
                {"canonical_video_start_ms": 10000, "canonical_video_end_ms": 900000}]},
        }
        (self.root / "ego4d.json").write_text(json.dumps({"videos": [self.video]}), encoding="utf-8")
        (self.root / "clips.csv").write_text(
            "exported_clip_uid,parent_video_uid,parent_start_sec,parent_end_sec,s3_path\n"
            "c0,parent,115.085,415.085,s3://fixture/c0.mp4\n", encoding="utf-8")
        self.stack.enter_context(patch.object(ego4d, "EGO4D_DIR", self.root))
        local_catalog = ego4d._catalog(str(self.root / "ego4d.json"), str(self.root / "clips.csv"))
        self.stack.enter_context(patch.object(ego4d, "_cat", return_value=local_catalog))
        self.stack.enter_context(patch.object(ego4d, "sync_meta", side_effect=AssertionError("offline only")))
        self.events(115.085, 415.085)

    def events(self, start, end):
        rows = [[start + i * 5, self.text] for i in range(int((end - start) / 5))]
        rows.append([end, self.text])
        (self.root / "timed_narrations.jsonl").write_text(
            json.dumps({"video_uid": "parent", "events": rows}) + "\n", encoding="utf-8")
        return rows

    def clip(self, start=115.085, end=415.085):
        return {"clip_uid": "c0", "exported_clip_uid": "c0", "parent_video_uid": "parent",
                "parent_start_sec": str(start), "parent_end_sec": str(end),
                "window_s": (start, end), "dur_s": end - start, "source": "ego4d"}

    def test_conflicting_preparation_coordinates_and_duration_are_rejected(self):
        clip = self.clip()
        signed = ego4d.attach_selection_evidence(clip, self.task)
        for changes in ({"parent_start_sec": "120", "parent_end_sec": "420"},
                        {"window_s": (120, 420)}, {"dur_s": 301}, {"dur_s": 300.0004},
                        {"parent_end_sec": "416.085"}):
            with self.subTest(changes=changes):
                changed = normalize_ego_clip({**signed, **changes})
                original = copy.deepcopy(changed)
                self.assertFalse(ego4d.task_window_has_temporal_evidence(self.task, changed))
                with self.assertRaises(ValueError):
                    ego4d._selection_candidate(changed)
                with self.assertRaises(ValueError):
                    ego4d.revalidate_selection_evidence(changed, task_name=self.task)
                self.assertEqual(changed, original)
        self.assertTrue(ego4d.task_window_has_temporal_evidence(self.task, clip))

    def test_imu_generated_endpoints_keep_annotation_bits_and_pass_gate(self):
        rule = task_matching.rule_for(self.task)
        for start, end, origin in ((115.085, 415.0854, 1.061), (287.224, 587.224, 8.012)):
            with self.subTest(start=start, end=end):
                rows = self.events(start, end)
                spans = task_matching.extract_spans(
                    rule, rows, min_s=60, max_s=600, activity_mode=True,
                    video_duration_s=900, allowed_intervals=[(origin, 900)])
                self.assertEqual(len(spans), 1)
                span = spans[0]
                self.assertEqual((span["start"], span["end"]), (start, end))
                record = ego4d._span_record(self.video, span)
                self.assertEqual(record["dur_s"], end - start)
                self.assertEqual(ego4d._selection_candidate(record)["window_s"], [start, end])
                self.assertTrue(ego4d.task_window_has_temporal_evidence(
                    self.task, self.clip(span["start"], span["end"])))
                # Old origin round-trip noise and exact-duration comparisons
                # must not exclude the source annotation anchors.
                noisy_start = math.nextafter(start, math.inf)
                noisy_end = math.nextafter(end, -math.inf)
                self.assertTrue(ego4d.task_window_has_temporal_evidence(
                    self.task, self.clip(noisy_start, noisy_end)))
                self.assertTrue(ego4d.task_window_has_temporal_evidence(
                    self.task, self.clip(start - 0.5e-6, end + 0.5e-6)))

    def test_millisecond_rounding_and_missing_activity_do_not_gain_evidence(self):
        start, end = 115.0854, 415.0854
        self.events(start, end)
        for a, b in ((round(start, 3), end), (start, round(end, 3)),
                     (start - 2e-6, end), (start, end + 2e-6), (120, 420)):
            with self.subTest(window=(a, b)):
                self.assertFalse(ego4d.task_window_has_temporal_evidence(self.task, self.clip(a, b)))
        self.text = "#C C cooks tacos"
        self.events(start, end)
        self.assertFalse(ego4d.task_window_has_temporal_evidence(self.task, self.clip(start, end)))

    def test_nonfinite_reversed_and_incomplete_coordinates_fail_closed(self):
        invalid = []
        for value in (math.nan, math.inf, -math.inf):
            invalid.extend(({"window_s": (value, 415.085)}, {"parent_end_sec": value}, {"dur_s": value}))
        invalid.extend(({"window_s": (415.085, 115.085)}, {"window_s": [115.085]},
                        {"parent_start_sec": None}, {"dur_s": 0}, {"dur_s": -1}))
        for changes in invalid:
            with self.subTest(changes=changes):
                clip = {**self.clip(), **changes}
                self.assertFalse(ego4d.task_window_has_temporal_evidence(self.task, clip))
                with self.assertRaises((ValueError, TypeError)):
                    ego4d._selection_candidate(clip)
