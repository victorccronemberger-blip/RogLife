from __future__ import annotations

import unittest
import gzip
import json

from moneymin import campaign
from moneymin import ego4d


class PortableTaskCatalogTests(unittest.TestCase):
    def embedded_hints(self):
        return json.loads(gzip.decompress(campaign._rank_seed_path().read_bytes()))["tasks"]

    def test_five_to_thirty_minute_task_hints_are_embedded_not_certified(self) -> None:
        # Artifact inventory is not current eligibility: old rounded durations
        # and parent scenarios cannot be promoted into current task evidence.
        seed = self.embedded_hints()
        for name in ("Furniture Assembly", "Cleaning Car", "Planting or Pulling Weeds",
                     "Trim a hedge", "Walk the Dog", "Watering Outdoor Plants",
                     "Clean the Bathroom", "Shopping",
                     "Folding Clothes or Putting Them on Hangers"):
            with self.subTest(name=name):
                self.assertTrue(any(300 <= float(c["dur_s"]) <= 1800
                                    for c in seed.get(name, ())))
        loaded = campaign._load_rank_seed()
        assert loaded is not None
        self.assertTrue(all(
            abs(float(c["dur_s"]) - (float(c["window_s"][1]) - float(c["window_s"][0]))) <= 1e-6
            and "selection_evidence" not in c
            for clips in loaded.values() for c in clips))

    def test_empty_legacy_cache_is_recovered_from_embedded_seed(self) -> None:
        seed = campaign._load_rank_seed()
        self.assertIsNotNone(seed)
        assert seed is not None

        recovered = campaign._merge_rank_seed({name: () for name in seed})

        self.assertEqual(
            sum(len(items) for items in recovered.values()),
            sum(len(items) for items in seed.values()),
        )
        self.assertGreater(sum(bool(items) for items in recovered.values()), 0)

    def test_local_clip_wins_and_seed_is_not_duplicated(self) -> None:
        seed = campaign._load_rank_seed()
        assert seed is not None
        name, clips = next((name, clips) for name, clips in seed.items() if clips)
        local = dict(clips[0], marker="local")

        recovered = campaign._merge_rank_seed({name: (local,)})

        matching = [
            clip for clip in recovered[name]
            if clip["clip_uid"] == local["clip_uid"]
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["marker"], "local")

    def test_duration_cache_is_completed_only_with_eligible_seed_clips(self) -> None:
        seed = campaign._load_rank_seed()
        assert seed is not None

        recovered = campaign._merge_rank_seed(
            {name: () for name in seed}, min_dur_s=60, max_dur_s=120)

        self.assertGreater(sum(len(items) for items in recovered.values()), 0)
        self.assertTrue(all(
            60 <= float(clip["dur_s"]) <= 120
            for items in recovered.values()
            for clip in items
        ))

    def test_legacy_long_seed_hints_do_not_bypass_coordinate_validation(self) -> None:
        seed = self.embedded_hints()

        long_clips = [
            clip
            for name, clips in seed.items()
            if name != "Gardening"
            for clip in clips
            if float(clip.get("dur_s") or 0) >= 600
        ]

        self.assertGreaterEqual(len(long_clips), 10)
        self.assertGreaterEqual(len({
            name
            for name, clips in seed.items()
            if name != "Gardening"
            if any(float(clip.get("dur_s") or 0) >= 600 for clip in clips)
        }), 5)
        loaded = campaign._load_rank_seed()
        assert loaded is not None
        recovered = campaign._merge_rank_seed(loaded)
        stale = {(name, c["clip_uid"]) for name, clips in seed.items() for c in clips
                 if abs(float(c["dur_s"]) - (float(c["window_s"][1]) - float(c["window_s"][0]))) > 1e-6}
        self.assertTrue(stale)
        self.assertFalse(stale & {(name, c["clip_uid"])
                                  for name, clips in recovered.items() for c in clips})

    def test_known_incomplete_sensor_sources_are_never_selected(self) -> None:
        seed = campaign._load_rank_seed()
        assert seed is not None

        recovered = campaign._merge_rank_seed(seed)

        self.assertFalse(any(
            str(clip.get("parent_video_uid") or "")
            in ego4d.KNOWN_INCOMPLETE_IMU_VIDEO_UIDS
            for clips in recovered.values()
            for clip in clips
        ))


if __name__ == "__main__":
    unittest.main()
