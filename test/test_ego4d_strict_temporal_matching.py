"""Scenario labels and aggregate captions cannot authorize an Ego4D interval."""
import copy
import gzip
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from moneymin import campaign, config, ego4d, task_matching


class StrictTemporalMatchingTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.media = self.root / "ego4d"
        self.media.mkdir()
        self.stack.enter_context(patch.object(config, "MEDIA_DATA_DIR", self.root))
        self.stack.enter_context(patch.object(ego4d, "EGO4D_DIR", self.media))
        self.stack.enter_context(patch.object(
            ego4d, "sync_meta", side_effect=AssertionError("Local annotations only")))
        self.stack.enter_context(patch.object(
            ego4d, "_cat", side_effect=lambda: ego4d._catalog(
                str(self.media / "ego4d.json"), str(self.media / "clips.csv"))))
        self.task = "Cleaning Car"

    def configure(self, events, scenarios=None):
        video = {"video_uid": "parent", "duration_sec": 1800, "has_imu": True,
                 "scenarios": scenarios or ["Car/scooter washing"],
                 "s3_path": "s3://fixture/parent.mp4",
                 "imu_metadata": {"s3_path": "s3://fixture/imu.csv", "component_metadata": [
                     {"canonical_video_start_ms": 0, "canonical_video_end_ms": 1800000}]}}
        (self.media / "ego4d.json").write_text(json.dumps({"videos": [video]}), encoding="utf-8")
        (self.media / "clips.csv").write_text(
            "exported_clip_uid,parent_video_uid,parent_start_sec,parent_end_sec,s3_path\n"
            "c0,parent,0,1800,s3://fixture/c0.mp4\n", encoding="utf-8")
        (self.media / "timed_narrations.jsonl").write_text(
            json.dumps({"video_uid": "parent", "events": events}) + "\n", encoding="utf-8")

    def clip(self, start, end):
        return {"clip_uid": "c0", "parent_video_uid": "parent", "needs_cut": True,
                "window_s": [start, end], "dur_s": end - start, "source": "ego4d",
                "s3_path": "s3://fixture/parent.mp4", "scenarios": ["Car/scooter washing"],
                "action_text": "#C C washes the car with a sponge",
                "action_units": ["#C C washes the car with a sponge"] * 20,
                "match_confidence": "scenario"}

    def test_historical_walking_and_scooter_patterns_are_not_car_cleaning(self):
        # Representative annotations from the two reported intervals, not a
        # substitute for the separate read-only check against the full dataset.
        cases = [
            (1062.074, 1405.91, [[1066.9, "#O man X walks around"],
                               [1069.1, "#C C walks around"],
                               [1087.8, "#C C walks through the exit"],
                               [1147.6, "#C C looks around"],
                               [1405.3, "#C C turns right"]]),
            (147.45643, 530.0738431948718, [[161.4, "#O A man H gets off the scooter"],
                                         [161.5, "#C C stops on the road"],
                                         [270.3, "#C C stops on the road"],
                                         [278.4, "#O a man H rides on the scooter"]]),
        ]
        for start, end, events in cases:
            with self.subTest(window=(start, end)):
                self.configure(events, ["Car/scooter washing", "Grocery shopping indoors",
                                        "Walking on street"])
                clip = self.clip(start, end)
                self.assertFalse(ego4d.task_window_has_temporal_evidence(self.task, clip))
                self.assertEqual(task_matching.ranked_clips(self.task, [clip]), [])
                self.assertEqual(task_matching.rank_all_tasks([clip]).get(self.task, []), [])
                self.assertEqual(ego4d.list_task_spans(self.task), [])

    def test_positive_temporal_activity_passes_but_unproven_tail_does_not(self):
        self.configure([[t, "#C C washes the car with a sponge"] for t in range(0, 121, 5)])
        self.assertTrue(ego4d.task_window_has_temporal_evidence(self.task, self.clip(0, 120)))
        for end in (180, 600, 900):
            with self.subTest(end=end):
                self.assertFalse(ego4d.task_window_has_temporal_evidence(self.task, self.clip(0, end)))
        self.assertEqual(ego4d.list_task_spans(self.task, min_dur_s=600), [])
        spans = ego4d.list_task_spans(self.task)
        self.assertTrue(spans)
        self.assertTrue(all(ego4d.task_window_has_temporal_evidence(self.task, c) for c in spans))
        self.assertTrue(all(c["window_s"][1] <= 120 for c in spans))

    def test_missing_or_out_of_window_annotations_never_use_aggregate_caption(self):
        self.configure([[t, "#C C washes the car with a sponge"] for t in range(0, 121, 5)])
        self.assertFalse(ego4d.task_window_has_temporal_evidence(self.task, self.clip(600, 900)))
        (self.media / "timed_narrations.jsonl").unlink()
        self.assertFalse(ego4d.task_window_has_temporal_evidence(self.task, self.clip(0, 120)))
        self.assertEqual(task_matching.ranked_clips(self.task, [self.clip(0, 120)]), [])

    def test_legacy_scenario_entry_points_cannot_grant_approval(self):
        rule = task_matching.rule_for(self.task)
        self.assertFalse(task_matching.scenario_is_sufficient(rule, ["Car/scooter washing"]))
        self.assertEqual(task_matching.scenario_activity_spans(
            [], min_s=300, max_s=1800, video_duration_s=1800), [])

    def test_inconsistent_portable_seed_rows_are_skipped_without_recertification(self):
        good = self.clip(0, 120.045)
        # An obsolete row first must not shadow a coherent row with the same ID.
        rounded = {**good, "dur_s": 120.0}
        nonfinite = {**good, "clip_uid": "nonfinite", "dur_s": float("nan")}
        invalid_window = {**good, "clip_uid": "backwards", "window_s": [120.045, 0]}
        payload = {"schema": 1, "tasks": {self.task: [rounded, good, nonfinite, invalid_window]}}
        original = copy.deepcopy(good)
        loaded = campaign._rank_seed_bytes_cached(gzip.compress(json.dumps(payload).encode("utf-8")))
        self.assertEqual(loaded[self.task], (original,))
        self.assertNotIn("selection_evidence", loaded[self.task][0])
        self.assertEqual(good, original)
