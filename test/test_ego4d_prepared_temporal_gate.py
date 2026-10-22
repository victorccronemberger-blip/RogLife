"""Prepared bytes/old approval never replace current local Ego4D task proof."""
import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from moneymin import campaign, config, ego4d
from moneymin.campaigns import catalog, preparation_engine
from moneymin.catalog_policy import prepared_catalog_read


class PreparedTemporalGateTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.media = self.root / "ego4d"
        self.media.mkdir()
        self.task = "Folding Clothes or Putting Them on Hangers"
        self.video = {
            "video_uid": "parent", "duration_sec": 600, "has_imu": True,
            "scenarios": ["Cleaning / laundry"],
            "s3_path": "s3://fixture/parent.mp4",
            "imu_metadata": {"s3_path": "s3://fixture/imu.csv", "component_metadata": [
                {"canonical_video_start_ms": 0, "canonical_video_end_ms": 600000}]},
        }
        self.meta = self.media / "ego4d.json"
        self.clips = self.media / "clips.csv"
        self.meta.write_text(json.dumps({"videos": [self.video]}), encoding="utf-8")
        self.clips.write_text(
            "exported_clip_uid,parent_video_uid,parent_start_sec,parent_end_sec,s3_path\n"
            "c0,parent,0,600,s3://fixture/c0.mp4\n", encoding="utf-8")
        (self.media / "clip_narrations.json").write_text(
            json.dumps({"c0": "#C C folds the shirt"}), encoding="utf-8")
        self.timed = self.media / "timed_narrations.jsonl"
        self.write_events([[t, "#C C folds the shirt"] for t in range(0, 601, 5)])
        self.stack.enter_context(patch.object(ego4d, "EGO4D_DIR", self.media))
        self.stack.enter_context(patch.object(config, "MEDIA_DATA_DIR", self.root))
        self.sync = self.stack.enter_context(patch.object(
            ego4d, "sync_meta", side_effect=AssertionError("No metadata acquisition")))
        self.cold = self.stack.enter_context(patch.object(
            ego4d, "rank_all_task_spans", side_effect=AssertionError("No cold classification")))
        self.download = self.stack.enter_context(patch.object(
            ego4d, "download_clip", side_effect=AssertionError("No media acquisition")))
        cat = ego4d._catalog(str(self.meta), str(self.clips))
        self.clip = ego4d._clip_record(cat.by_uid["c0"], cat.videos["parent"])
        # Inert legacy prepared artifacts, not real media or a provider receipt.
        self.prepared = self.root / "prepared"
        self.prepared.mkdir()
        (self.prepared / "c0_native.mp4").write_bytes(b"old prepared fixture")
        (self.prepared / "c0_native.cache.json").write_text('{"version": 4}', encoding="utf-8")
        self.original_bytes = {p.name: p.read_bytes() for p in self.prepared.iterdir()}

    def write_events(self, events):
        self.timed.write_text(json.dumps({"video_uid": "parent", "events": events}) + "\n",
                              encoding="utf-8")

    def signed(self):
        return ego4d.attach_selection_evidence(self.clip, self.task)

    def assert_rejected_before_effects(self, clip):
        original = copy.deepcopy(clip)
        with patch.object(campaign, "_ego_prepare_plan") as plan, \
             patch.object(campaign, "_normalize_video") as encode:
            with self.assertRaises(ValueError):
                campaign.prepare_clip(clip, self.video, self.prepared, allow_download=False)
            plan.assert_not_called()
            encode.assert_not_called()
        self.assertEqual(clip, original)
        self.assertEqual({p.name: p.read_bytes() for p in self.prepared.iterdir()}, self.original_bytes)
        self.download.assert_not_called()
        self.sync.assert_not_called()
        self.cold.assert_not_called()

    def test_legacy_without_carrier_is_not_admitted_even_with_good_annotations(self):
        for carrier in (None, {}, "legacy"):
            clip = {**self.clip, "selection_evidence": carrier}
            with self.subTest(carrier=carrier), prepared_catalog_read():
                self.assertFalse(campaign._prepare_queue_accepts(clip, self.task))
                self.assert_rejected_before_effects(clip)
        clip = dict(self.clip)
        self.assertNotIn("selection_evidence", clip)
        self.assertFalse(campaign._prepare_queue_accepts(clip, self.task))
        self.assert_rejected_before_effects(clip)

    def test_old_algorithm_carrier_does_not_reapprove_prepared_bytes(self):
        clip = self.signed()
        evidence = clip["selection_evidence"]
        evidence["algorithm_version"] = "ego4d-selection-content-v3"
        evidence["evidence_sha256"] = ego4d._selection_digest(
            {key: value for key, value in evidence.items() if key != "evidence_sha256"})
        self.assertFalse(campaign._prepare_queue_accepts(clip, self.task))
        self.assert_rejected_before_effects(clip)

    def test_current_carrier_without_timed_evidence_fails_despite_official_action(self):
        self.timed.unlink()
        clip = self.signed()  # Valid bindings, but no temporal support.
        self.assertFalse(ego4d.task_window_has_temporal_evidence(self.task, clip))
        with prepared_catalog_read():
            self.assertFalse(campaign._prepare_queue_accepts(clip, self.task, fresh=False))
        self.assert_rejected_before_effects(clip)

    def test_other_export_cannot_replace_source_under_an_intact_carrier(self):
        self.clips.write_text(
            "exported_clip_uid,parent_video_uid,parent_start_sec,parent_end_sec,s3_path\n"
            "c0,parent,0,300,s3://fixture/c0.mp4\n"
            "c1,parent,300,600,s3://fixture/c1.mp4\n", encoding="utf-8")
        self.write_events([[t, "#C C folds the shirt"] for t in range(0, 301, 5)]
                          + [[t, "#C C cooks tacos"] for t in range(305, 601, 5)])
        current = ego4d._catalog(str(self.meta), str(self.clips))
        self.clip = ego4d._clip_record(current.by_uid["c0"], current.videos["parent"])
        signed = self.signed()
        self.assertTrue(campaign._prepare_queue_accepts(signed, self.task))
        changed = {**signed, "exported_clip_uid": "c1"}
        self.assertEqual(changed["selection_evidence"], signed["selection_evidence"])
        self.assertFalse(campaign._prepare_queue_accepts(changed, self.task))
        with self.assertRaises(ValueError):
            ego4d.revalidate_selection_evidence(changed, task_name=self.task)
        self.assert_rejected_before_effects(changed)

    def test_sparse_timed_evidence_cannot_approve_a_whole_prepared_window(self):
        self.write_events([[0, "#C C folds the shirt"], [600, "#C C folds the shirt"]])
        clip = self.signed()
        self.assertFalse(ego4d.task_window_has_temporal_evidence(self.task, clip))
        self.assertFalse(campaign._prepare_queue_accepts(clip, self.task))
        self.assert_rejected_before_effects(clip)

    def test_changed_annotations_invalidate_previously_accepted_prepared_clip(self):
        clip = self.signed()
        self.assertTrue(campaign._prepare_queue_accepts(clip, self.task))
        self.write_events([[t, "#C C cooks tacos"] for t in range(0, 601, 5)])
        self.assertFalse(campaign._prepare_queue_accepts(clip, self.task))
        self.assert_rejected_before_effects(clip)
        # Rebinding alone still cannot turn unrelated annotations into proof.
        rebound = self.signed()
        self.assertFalse(campaign._prepare_queue_accepts(rebound, self.task))
        self.assert_rejected_before_effects(rebound)

    def test_current_exact_window_passes_read_only_gate_without_mutating_identity(self):
        clip = self.signed()
        original = copy.deepcopy(clip)
        with prepared_catalog_read(), patch.object(
                ego4d, "task_window_has_temporal_evidence",
                wraps=ego4d.task_window_has_temporal_evidence) as temporal:
            self.assertTrue(campaign._prepare_queue_accepts(clip, self.task, fresh=False))
            temporal.assert_called()
        self.assertEqual(clip, original)
        self.sync.assert_not_called()
        self.cold.assert_not_called()
        self.download.assert_not_called()

    def test_preparation_requires_fresh_validation_even_when_media_is_prepared(self):
        clip = self.signed()
        # Stop immediately after the gate, without executing a media operation.
        services = SimpleNamespace(
            _normalize_ego_clip_for_prepare=lambda row: row,
            ego4d=ego4d, Path=Mock(side_effect=RuntimeError("past fresh gate")))
        with patch.object(ego4d, "revalidate_selection_evidence",
                          wraps=ego4d.revalidate_selection_evidence) as validate:
            with self.assertRaisesRegex(RuntimeError, "past fresh gate"):
                preparation_engine.prepare_clip(services, clip, self.video, self.prepared,
                                                allow_download=False)
            validate.assert_called_once_with(clip)
        self.assertEqual({p.name: p.read_bytes() for p in self.prepared.iterdir()}, self.original_bytes)

    def test_nymeria_and_holoassist_keep_their_existing_admission_contracts(self):
        nymeria = Mock()
        ego = Mock()
        services = SimpleNamespace(nymeria=nymeria, ego4d=ego)
        for planned in (False, True):
            clip = {"source": "nymeria", "clip_uid": "nymeria:fixture",
                    "acquisition_required": planned}
            self.assertTrue(catalog._prepare_queue_accepts(services, clip, self.task))
        nymeria.revalidate_candidate.assert_called_once_with(
            {"source": "nymeria", "clip_uid": "nymeria:fixture", "acquisition_required": False},
            task_name=self.task, fresh=True)
        nymeria.revalidate_planned_candidate.assert_called_once()
        nymeria.revalidate_candidate.side_effect = ValueError("stale Nymeria")
        self.assertFalse(catalog._prepare_queue_accepts(
            services, {"clip_uid": "nymeria:legacy"}, self.task))
        self.assertTrue(catalog._prepare_queue_accepts(
            services, {"source": "holoassist"}, self.task))
        ego.revalidate_selection_evidence.assert_not_called()


if __name__ == "__main__":
    unittest.main()
