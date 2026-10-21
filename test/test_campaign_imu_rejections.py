"""Sensor-gap rejections persist the strictest failed minimum and block reuse."""
import json
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from moneymin import campaign, campaign_plan, config, ego4d, imu_coverage
from moneymin.campaign_types import AccountSpec, CampaignConfig, TaskSpec
from moneymin.campaigns import imu_rejections


def _entry(*, min_dur_s=180.0, longest_continuous_s=9.996, task_id="dog",
           task_name="Walk the Dog"):
    return {"min_dur_s": min_dur_s, "longest_continuous_s": longest_continuous_s,
            "detail": "ValueError: cobertura IMU insuficiente",
            "recorded_at": "2026-10-20T00:00:00+00:00",
            "task_id": task_id, "task_name": task_name}


class ImuRejectionsStoreTests(unittest.TestCase):
    def test_failed_middle_does_not_block_larger_alias_with_valid_sensor_window(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config, "DATA_DIR", Path(folder)):
            root = Path(folder)
            source = root / 'parent_imu.csv'
            with source.open('w', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['canonical_timestamp_ms', 'gyro_x', 'gyro_y', 'gyro_z',
                                 'accl_x', 'accl_y', 'accl_z'])
                # A valid 130-second opening, followed by sparse/discontinuous
                # measurements. The rejected middle cannot condemn the opening.
                for timestamp in [*range(0, 130001, 5), *range(131000, 300001, 1000)]:
                    writer.writerow([timestamp, 1, 2, 3, 4, 5, 6])
            failed = {'clip_uid': 'parent_140.000_280.000', 'parent_video_uid': 'parent',
                      'source': 'ego4d', 'window_s': [140, 280]}
            longest = imu_rejections.longest_continuous_s(failed, root, max_dur_s=300)
            self.assertLess(longest, 120)
            imu_rejections.record(failed, min_dur_s=120, longest_continuous_s=longest,
                                  detail='sensor gap', task_id='dog', task_name='Walk the Dog')
            registry = imu_rejections.load()
            candidate = {'clip_uid': 'parent_0.000_300.000', 'parent_video_uid': 'parent',
                         'source': 'ego4d', 'window_s': [0, 300],
                         'dedup_clip_uids': [failed['clip_uid']]}
            before = (root / imu_rejections.state_file_name()).read_bytes()
            self.assertIsNone(imu_rejections.blocking_entry(registry, candidate, 120))
            with patch.object(ego4d, 'find_clip', return_value=(None, None)):
                carved = imu_coverage.carve_continuous_window(candidate, root, min_s=120, max_s=300)
            self.assertIsNotNone(carved)
            self.assertGreaterEqual(carved['dur_s'], 120)
            self.assertLess(carved['window_s'][1], 131)
            ego4d.build_imu_csv(source, carved['window_s'], validate_only=True)
            cfg = CampaignConfig([AccountSpec('fresh@example.invalid', 'org')],
                [TaskSpec('dog', 'Walking the dog / pet', 120, 600, task_name='Walk the Dog')],
                dataset_provider='ego4d')
            with patch.object(campaign, 'automatic_candidates', return_value=[candidate]):
                _, review, _ = campaign_plan.build(cfg)
            self.assertEqual(review[0]['eligible_accounts'], ['fresh@example.invalid'])
            self.assertEqual((root / imu_rejections.state_file_name()).read_bytes(), before)

    def test_alias_requires_same_parent_and_full_containment_of_measured_window(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config, 'DATA_DIR', Path(folder)):
            failed = {'clip_uid': 'opaque-export', 'parent_video_uid': 'parent', 'window_s': [100, 300]}
            imu_rejections.record(failed, min_dur_s=120, longest_continuous_s=10,
                                  detail='sensor gap', task_id='dog', task_name='Walk the Dog')
            registry = imu_rejections.load()
            cases = [('parent', [150, 280], True), ('parent', [100, 300], True),
                     ('parent', [50, 350], False), ('parent', [50, 150], False),
                     ('parent', [350, 500], False), ('different', [150, 280], False),
                     ('parent', None, False), ('parent', [float('nan'), 280], False)]
            for parent, window, expected in cases:
                with self.subTest(parent=parent, window=window):
                    candidate = {'clip_uid': 'other', 'parent_video_uid': parent,
                                 'window_s': window, 'dedup_clip_uids': ['opaque-export']}
                    self.assertEqual(imu_rejections.blocking_entry(registry, candidate, 120)
                                     is not None, expected)
            self.assertIsNotNone(imu_rejections.blocking_entry(registry, failed, 120))

    def test_legacy_rounded_window_is_used_conservatively_without_rewriting_store(self):
        uid = 'parent_100.000_300.000'
        registry = {uid: _entry(min_dur_s=120)}
        candidate = {'clip_uid': 'other', 'parent_video_uid': 'parent',
                     'dedup_clip_uids': [uid], 'window_s': [110, 280]}
        self.assertIsNotNone(imu_rejections.blocking_entry(registry, candidate, 120))
        # The old ID was rounded, so its apparent edges cannot exclude a
        # candidate extending outside the actual, unknown measurement boundary.
        for window in ([100, 300], [50, 350], [float('inf'), 400]):
            with self.subTest(window=window):
                self.assertIsNone(imu_rejections.blocking_entry(
                    registry, {**candidate, 'window_s': window}, 120))
        inconsistent = {uid: {**registry[uid], 'window_s': [100, 300]}}
        self.assertIsNone(imu_rejections.blocking_entry(inconsistent, candidate, 120))

    def test_record_keeps_the_strictest_minimum_without_guessing_alias_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(config, "DATA_DIR", Path(folder)):
                clip = {"clip_uid": "clip", "dedup_clip_uids": ["alias"]}
                imu_rejections.record(
                    clip, min_dur_s=180, longest_continuous_s=9.996,
                    detail="ValueError: cobertura IMU insuficiente",
                    task_id="dog", task_name="Walk the Dog")
                registry = imu_rejections.load()
                self.assertEqual(registry["clip"]["min_dur_s"], 180)
                self.assertEqual(registry["clip"]["task_name"], "Walk the Dog")
                # A dedup alias may overlap only part of the failed window.
                # An opaque old ID alone cannot prove that sensors also fail here.
                self.assertIsNone(imu_rejections.blocking_entry(
                    registry, {"clip_uid": "other", "dedup_clip_uids": ["clip"]}, 180))
                self.assertIsNone(imu_rejections.blocking_entry(
                    registry, {"clip_uid": "other", "dedup_clip_uids": ["alias"]}, 180))
                self.assertIsNotNone(imu_rejections.blocking_entry(
                    registry, {"clip_uid": "clip"}, 300))
                # Um pedido mais brando que a falha registrada pode tentar de novo.
                self.assertIsNone(imu_rejections.blocking_entry(
                    registry, {"clip_uid": "clip"}, 60))
                # Mínimo maior não sobrescreve a falha já registrada...
                again = imu_rejections.record(
                    clip, min_dur_s=300, longest_continuous_s=250,
                    detail="other", task_id="dog", task_name="Walk the Dog")
                self.assertEqual(again["min_dur_s"], 180)
                self.assertEqual(imu_rejections.load()["clip"]["min_dur_s"], 180)
                # ...e um mínimo menor atualiza: o pedido brando também já falhou.
                lowered = imu_rejections.record(
                    clip, min_dur_s=30, longest_continuous_s=9.996,
                    detail="other", task_id="dog", task_name="Walk the Dog")
                self.assertEqual(lowered["min_dur_s"], 30)
                updated = imu_rejections.load()
                self.assertIsNotNone(imu_rejections.blocking_entry(
                    updated, {"clip_uid": "clip"}, 30))
                self.assertIsNone(imu_rejections.blocking_entry(
                    updated, {"clip_uid": "clip"}, 20))
                # Sem lacuna real (medição ≥ mínimo) nada é gravado.
                self.assertIsNone(imu_rejections.record(
                    {"clip_uid": "fine"}, min_dur_s=30, longest_continuous_s=30,
                    detail="d", task_id="dog", task_name="Walk the Dog"))
                self.assertNotIn("fine", imu_rejections.load())

    def test_corrupt_or_malformed_state_is_never_treated_as_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(config, "DATA_DIR", Path(folder)):
                path = Path(folder) / imu_rejections.state_file_name()
                payloads = (
                    b"broken", b"[]", b'{"clip":{"min_dur_s":180}}',
                    b'{"":{"min_dur_s":1,"longest_continuous_s":0,"detail":"d",'
                    b'"recorded_at":"x","task_id":"t","task_name":"n"}}',
                    b'{"clip":{"min_dur_s":180,"longest_continuous_s":0,"detail":"",'
                    b'"recorded_at":"x","task_id":"t","task_name":"n"}}',
                )
                for payload in payloads:
                    with self.subTest(payload=payload):
                        path.write_bytes(payload)
                        with self.assertRaises(ValueError):
                            imu_rejections.load()
                        with self.assertRaises(ValueError):
                            imu_rejections.record(
                                {"clip_uid": "clip"}, min_dur_s=30, longest_continuous_s=10,
                                detail="d", task_id="t", task_name="n")
                        self.assertEqual(path.read_bytes(), payload)

    def test_absent_store_is_empty_and_review_limits_eligibility(self):
        clip = {"clip_uid": "clip", "source": "ego4d", "parent_video_uid": "parent", "dur_s": 300}
        with tempfile.TemporaryDirectory() as folder, patch.object(config, "DATA_DIR", Path(folder)):
            self.assertEqual(imu_rejections.load(), {})
            cfg = CampaignConfig(
                [AccountSpec("fresh@example.com", "org")],
                [TaskSpec("dog", "Walking the dog / pet", 180, 780, task_name="Walk the Dog")],
                dataset_provider="ego4d")
            with patch.object(campaign, "automatic_candidates", return_value=[clip]):
                _, review, _ = campaign_plan.build(cfg)
            self.assertEqual(review[0]["eligible_accounts"], ["fresh@example.com"])
            self.assertNotEqual(review[0]["exclusion_reason"], "sensor_gap")
            (Path(folder) / imu_rejections.state_file_name()).write_text(
                json.dumps({"clip": _entry()}), encoding="utf-8")
            with patch.object(campaign, "automatic_candidates", return_value=[clip]):
                _, review, _ = campaign_plan.build(cfg)
            self.assertEqual(review[0]["eligible_accounts"], [])
            self.assertEqual(review[0]["exclusion_reason"], "sensor_gap")
            # Um pedido mais brando que a falha registrada volta a considerar o clipe.
            cfg.tasks = [TaskSpec("dog", "Walking the dog / pet", 60, 780, task_name="Walk the Dog")]
            with patch.object(campaign, "automatic_candidates", return_value=[clip]):
                _, review, _ = campaign_plan.build(cfg)
            self.assertEqual(review[0]["eligible_accounts"], ["fresh@example.com"])
            self.assertNotEqual(review[0]["exclusion_reason"], "sensor_gap")

    def test_admission_policy_versions_the_store_and_keeps_the_legacy_ledger(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config, "DATA_DIR", Path(folder)):
            root = Path(folder)
            legacy = root / imu_rejections.FILE_NAME
            legacy_payload = json.dumps({"clip": _entry(min_dur_s=120)})
            legacy.write_text(legacy_payload, encoding="utf-8")
            # O ledger da política antiga (25 ms) permanece como auditoria e não
            # bloqueia janelas reavaliadas sob a tolerância atual (250 ms).
            self.assertEqual(imu_rejections.load(), {})
            imu_rejections.record(
                {"clip_uid": "now", "parent_video_uid": "p", "window_s": [10, 280]},
                min_dur_s=120, longest_continuous_s=90, detail="sensor gap",
                task_id="dog", task_name="Walk the Dog")
            active = root / imu_rejections.state_file_name()
            self.assertTrue(active.is_file())
            self.assertEqual(set(json.loads(active.read_text(encoding="utf-8"))), {"now"})
            self.assertEqual(legacy.read_text(encoding="utf-8"), legacy_payload)
            self.assertIsNotNone(imu_rejections.blocking_entry(imu_rejections.load(),
                {"clip_uid": "now", "parent_video_uid": "p", "window_s": [10, 280]}, 120))


if __name__ == "__main__":
    unittest.main()
