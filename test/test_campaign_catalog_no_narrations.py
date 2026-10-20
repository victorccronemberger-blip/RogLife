"""Campaign catalog fallback behavior when Ego4D narrations are absent."""
from contextlib import ExitStack
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from moneymin import campaign, ego4d
from moneymin.catalog_policy import CatalogPreparationRequired, persist_catalog_indexes


class CampaignCatalogWithoutNarrationsTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(campaign.config, "DATA_DIR", self.root))
        self.stack.enter_context(patch.object(campaign.config, "MEDIA_DATA_DIR", self.root / "library"))
        self.stack.enter_context(patch.object(ego4d, "EGO4D_DIR", self.root / "library" / "ego4d"))
        campaign._RANK_INPUT_SIGNATURE = None
        self.addCleanup(setattr, campaign, "_RANK_INPUT_SIGNATURE", None)
        for func in (campaign._rank_cache_stamp, campaign._ranked_pools_cached,
                     campaign._duration_ranked_pools, campaign._load_rank_seed):
            func.cache_clear()
            self.addCleanup(func.cache_clear)

    @staticmethod
    def clip(uid, source, duration):
        return {
            "clip_uid": uid,
            "source": source,
            "dur_s": float(duration),
            "window_s": [0.0, float(duration)],
            "selection_ready": True,
        }

    def test_available_tasks_all_keeps_prepared_provider_rows_without_narrations(self):
        tasks = [
            {"id": "ego", "name": "Walk the Dog"},
            {"id": "nymeria", "name": "Brew Coffee or Tea"},
            {"id": "holo", "name": "Furniture Assembly"},
        ]
        ego_clip = self.clip("ego-indexed", "ego4d", 150)
        pools = {"Walk the Dog": (ego_clip,)}

        def nymeria_windows(name, minimum, maximum, *, catalog_only=False):
            self.assertTrue(catalog_only)
            self.assertGreaterEqual(120, minimum)
            self.assertLessEqual(120, maximum)
            return (self.clip("nymeria-indexed", "nymeria", 120),) if name == "Brew Coffee or Tea" else ()

        def holo_clips(name, *, min_dur_s, max_dur_s):
            return ((self.clip("holo-indexed", "holoassist", 90),)
                    if name == "Furniture Assembly" and min_dur_s <= 90 <= max_dur_s else ())

        with patch.object(campaign, "_local_narration_catalog", return_value=False), \
             patch.object(campaign, "_ranked_pools", return_value=pools), \
             patch.object(campaign, "_task_candidates", side_effect=AssertionError("cold Ego4D lookup")), \
             patch.object(ego4d, "ensure_task_annotations", side_effect=AssertionError("cold annotation acquisition")), \
             patch.object(campaign, "_nymeria_windows", side_effect=nymeria_windows), \
             patch.object(campaign.holoassist, "list_clips", side_effect=holo_clips), \
             patch.object(campaign, "_catalog_queue_accepts", return_value=True), \
             patch.object(campaign, "_catalog_clip_cached_hint", return_value=True):
            for minimum, maximum, mode in ((60, 1800, "dataset"), (100, 180, "cache"), (80, 200, "both")):
                with self.subTest(range=(minimum, maximum), mode=mode):
                    rows = campaign.available_tasks(
                        "fixture@example.invalid", "org", remote_tasks=tasks,
                        min_dur_s=minimum, max_dur_s=maximum,
                        dataset_provider="all", content_mode=mode, include_unavailable=True)
                    by_id = {row["id"]: row for row in rows}
                    self.assertEqual(by_id["ego"]["clip_count"], int(minimum <= 150 <= maximum))
                    self.assertEqual(by_id["nymeria"]["clip_count"], int(minimum <= 120 <= maximum))
                    self.assertEqual(by_id["holo"]["clip_count"], int(minimum <= 90 <= maximum))
                    if minimum <= 150 <= maximum:
                        self.assertEqual(by_id["ego"]["clip_sources"], {"ego4d": 1})
                    if minimum <= 120 <= maximum:
                        self.assertEqual(by_id["nymeria"]["clip_sources"], {"nymeria": 1})
                    if minimum <= 90 <= maximum:
                        self.assertEqual(by_id["holo"]["clip_sources"], {"holoassist": 1})

    def test_missing_or_stale_prepared_ego_index_does_not_hide_other_providers(self):
        task = [{"id": "holo", "name": "Furniture Assembly"}]
        with patch.object(campaign, "_local_narration_catalog", return_value=False), \
             patch.object(campaign, "_ranked_pools", side_effect=CatalogPreparationRequired("stale index")), \
             patch.object(campaign, "_task_candidates", side_effect=AssertionError("cold Ego4D lookup")), \
             patch.object(ego4d, "ensure_task_annotations", side_effect=AssertionError("cold annotation acquisition")), \
             patch.object(campaign.holoassist, "list_clips", return_value=(self.clip("holo-indexed", "holoassist", 90),)), \
             patch.object(campaign, "_nymeria_windows", return_value=()), \
             patch.object(campaign, "_catalog_queue_accepts", return_value=True):
            rows = campaign.available_tasks("fixture@example.invalid", "org", remote_tasks=task,
                dataset_provider="all", content_mode="dataset", include_unavailable=True)
        self.assertEqual(rows[0]["clip_count"], 1)
        self.assertEqual(rows[0]["clip_sources"], {"holoassist": 1})

    def test_real_ranked_pools_facade_requires_prepared_index_without_narrations(self):
        # No narration files or ranked cache exist under this isolated root.
        # The production facade must observe prepared_catalog_read and refuse
        # to acquire annotations before the catalog can retain HoloAssist.
        task = [{"id": "holo", "name": "Furniture Assembly"}]
        with patch.object(ego4d, "ensure_task_annotations",
                          side_effect=AssertionError("prepared read tried to acquire annotations")) as ensure, \
             patch.object(campaign, "_task_candidates",
                          side_effect=AssertionError("prepared read tried cold candidates")), \
             patch.object(campaign, "_nymeria_windows", return_value=()), \
             patch.object(campaign.holoassist, "list_clips",
                          return_value=(self.clip("holo-indexed", "holoassist", 90),)), \
             patch.object(campaign, "_catalog_queue_accepts", return_value=True):
            rows = campaign.available_tasks(
                "fixture@example.invalid", "org", remote_tasks=task,
                dataset_provider="all", content_mode="dataset", include_unavailable=True)
        self.assertEqual(ensure.call_count, 0)
        self.assertEqual(rows[0]["clip_count"], 1)
        self.assertEqual(rows[0]["clip_sources"], {"holoassist": 1})

    def test_explicit_index_persistence_and_normal_expansion_keep_existing_behavior(self):
        with patch.object(campaign, "_local_narration_catalog", return_value=False), \
             patch.object(campaign, "_ranked_pools", return_value={}), \
             patch.object(campaign, "_task_candidates", return_value=()), \
             patch.object(campaign, "_nymeria_windows", return_value=()), \
             patch.object(campaign.holoassist, "list_clips", return_value=()), \
             patch.object(ego4d, "ensure_task_annotations") as ensure:
            with persist_catalog_indexes():
                campaign._compatible_task_clips("Walk the Dog", "all", catalog_only=True)
            self.assertEqual(ensure.call_count, 1)
            ensure.reset_mock()
            campaign._compatible_task_clips("Walk the Dog", "all", catalog_only=False)
            self.assertEqual(ensure.call_count, 1)

    def test_unexpected_prepared_index_error_is_not_hidden(self):
        failure = OSError("fixture index unreadable")
        with patch.object(campaign, "_local_narration_catalog", return_value=False), \
             patch.object(campaign, "_ranked_pools", side_effect=failure), \
             patch.object(campaign, "_task_candidates", side_effect=AssertionError("cold Ego4D lookup")), \
             patch.object(ego4d, "ensure_task_annotations", side_effect=AssertionError("cold annotation acquisition")), \
             patch.object(campaign, "_nymeria_windows", return_value=()), \
             patch.object(campaign.holoassist, "list_clips", return_value=()):
            with self.assertRaisesRegex(OSError, "fixture index unreadable"):
                campaign._compatible_task_clips("Walk the Dog", "all", catalog_only=True)


if __name__ == "__main__":
    unittest.main()
