"""Reviewed categories do not change source selection or media readiness."""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from moneymin import holoassist, holo_accelerator, nymeria_library
from moneymin import provider_category_reviews as reviews


class ProviderCategoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.resource = self.root / "reviews.json"
        self.stack.enter_context(patch.object(reviews, "_RESOURCE_PATH", self.resource))
        self.stack.enter_context(patch.object(holoassist, "data_dir", return_value=self.root / "holo"))
        self.addCleanup(nymeria_library._clear_inventory_cache)
        self.record = {"video_name": "reviewed-table", "taskType": "assemble tray table",
            "videoMetadata": {"duration": {"seconds": 109}},
            "events": [{"label": "Fine grained action", "start": 0, "end": 109,
                        "attributes": {"Action Correctness": "Correct Action"}}]}
        self.summary = b"start_time,end_time,Describe my activity\n100,130,C folds clothes.\n"
        self.write_resource()

    def write_resource(self):
        digest = hashlib.sha256(json.dumps(self.record, sort_keys=True, ensure_ascii=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        self.resource.write_text(json.dumps({"schema": 1, "providers": {
            "holoassist": {"reviewed-table": {"status": "confirmed",
                "task_names": ["Furniture Assembly", "Furniture Assembly/ Disassembly"],
                "annotation_sha256": digest, "source_video_sha256": "a" * 64,
                "source_video_bytes": 4096,
                "evidence": {"method": "Visual review of source images.",
                    "sampled_images": 110, "duration_s": 109.82,
                    "description": "Visible assembly of table legs and tabletop."}}},
            "nymeria": {"summary-only": {"status": "suggested_from_summary",
                "suggested_task_names": ["Folding Clothes or Putting Them on Hangers"],
                "annotation_sha1": hashlib.sha1(self.summary).hexdigest(),
                "evidence": {"method": "Official activity summary, no video review.",
                    "description": "Clothes folding is a review lead only.",
                    "summary_rows": [1]}}}}}), encoding="utf-8")

    def test_holo_category_confirmed_while_cache_stays_unprepared(self):
        with patch.object(holoassist, "annotations", return_value=(self.record,)), \
             patch.object(holoassist, "_indexed_video_names", return_value={"reviewed-table"}), \
             patch.object(holo_accelerator, "clip_ready", return_value=False):
            clip = holoassist.list_clips("Furniture Assembly")[0]
            self.assertTrue(clip["category_review"]["confirmed"])
            self.assertEqual(clip["category_review"]["asset_binding"], "not_checked")
            status = holo_accelerator.cache_status()
            self.assertEqual(status["ready"], 0)
            self.assertEqual(status["pending"], 1)
            self.assertEqual(status["category_review_summary"]["confirmed"], 1)
            # Both destination aliases refer to one recording.
            report = holoassist.report()
            self.assertEqual(report["category_review_summary"]["confirmed"], 1)
            self.assertEqual(report["category_review_summary"]["pending"], 0)

    def test_review_cannot_override_quality_or_wrong_task(self):
        with patch.object(holoassist, "annotations", return_value=(self.record,)), \
             patch.object(holoassist, "_indexed_video_names", return_value={"reviewed-table"}):
            self.assertEqual(holoassist.list_clips("Brew Coffee or Tea"), [])
            self.record["events"][0]["attributes"]["Action Correctness"] = "Wrong Action, not corrected"
            self.assertEqual(holoassist.list_clips("Furniture Assembly"), [])

    def test_annotation_change_removes_historical_confirmation(self):
        self.record["taskType"] = "disassemble tray table"
        with patch.object(holoassist, "annotations", return_value=(self.record,)), \
             patch.object(holoassist, "_indexed_video_names", return_value={"reviewed-table"}):
            clip = holoassist.list_clips("Furniture Assembly/ Disassembly")[0]
        self.assertFalse(clip["category_review"]["confirmed"])
        self.assertEqual(clip["category_review"]["task_names"], [])

    def sequence(self):
        directory = self.root / "summary-only"
        (directory / "narration").mkdir(parents=True, exist_ok=True)
        (directory / "metadata.json").write_text(json.dumps({"uid": "summary-only",
            "script": "Laundry", "head_duration_sec": 120}), encoding="utf-8")
        (directory / "narration/activity_summarization.csv").write_bytes(self.summary)
        groups = {name: {"file_size_bytes": 100} for name in nymeria_library._GROUPS}
        return directory, groups

    def test_summary_lead_is_visible_without_becoming_measured_or_atomic(self):
        directory, groups = self.sequence()
        item = nymeria_library._sequence_item(self.root, directory.name, groups)
        self.assertEqual(item["state"], "no_annotations")
        self.assertFalse(item["selection_ready"])
        self.assertEqual(item["atomic_action_count"], 0)
        review = item["category_review"]
        self.assertFalse(review["confirmed"])
        self.assertEqual(review["status"], "suggested_from_summary")
        self.assertEqual(review["task_names"], [])
        self.assertEqual(review["suggested_task_names"], ["Folding Clothes or Putting Them on Hangers"])
        (directory / "narration/activity_summarization.csv").write_text(
            "start_time,end_time,Describe my activity\n100,130,C plays chess.\n", encoding="utf-8")
        stale = nymeria_library._sequence_item(self.root, directory.name, groups)
        self.assertEqual(stale["category_review"]["suggested_task_names"], [])
        self.assertFalse(stale["selection_ready"])

    def test_inventory_cache_invalidates_when_review_resource_changes(self):
        directory, groups = self.sequence()
        sequences = {directory.name: groups}
        with patch.object(nymeria_library.nymeria, "_rules_digest", return_value="fixture"), \
             patch.object(nymeria_library.nymeria, "_sdk_signature", return_value=()):
            initial = nymeria_library._inventory_items(self.root, sequences)
            self.assertEqual(initial[0]["category_review"]["status"], "suggested_from_summary")
            resource = json.loads(self.resource.read_text(encoding="utf-8"))
            resource["providers"]["nymeria"] = {}
            self.resource.write_text(json.dumps(resource), encoding="utf-8")
            current = nymeria_library._inventory_items(self.root, sequences)
        self.assertEqual(current[0]["category_review"]["status"], "pending")
        self.assertEqual(current[0]["state"], initial[0]["state"])
        self.assertFalse(current[0]["selection_ready"])


if __name__ == "__main__":
    unittest.main()
