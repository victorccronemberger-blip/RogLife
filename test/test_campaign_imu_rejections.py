"""Sensor-gap rejections persist the strictest failed minimum and block reuse."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from moneymin import campaign, campaign_plan, config
from moneymin.campaign_types import AccountSpec, CampaignConfig, TaskSpec
from moneymin.campaigns import imu_rejections


def _entry(*, min_dur_s=180.0, longest_continuous_s=9.996, task_id="dog",
           task_name="Walk the Dog"):
    return {"min_dur_s": min_dur_s, "longest_continuous_s": longest_continuous_s,
            "detail": "ValueError: cobertura IMU insuficiente",
            "recorded_at": "2026-10-20T00:00:00+00:00",
            "task_id": task_id, "task_name": task_name}


class ImuRejectionsStoreTests(unittest.TestCase):
    def test_record_keeps_the_strictest_minimum_and_blocks_aliases(self):
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
                # Um candidato posterior que lista a uid registrada entre seus
                # aliases também é bloqueado (mesma gravação sob outra identidade).
                self.assertIsNotNone(imu_rejections.blocking_entry(
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
                path = Path(folder) / imu_rejections.FILE_NAME
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
            (Path(folder) / imu_rejections.FILE_NAME).write_text(
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


if __name__ == "__main__":
    unittest.main()