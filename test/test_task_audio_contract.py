"""Minute 1.29 audio requirements across catalog, reviewed pool and delivery."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from moneymin import campaign, minute_api, sidecar
from moneymin.campaign_types import AccountSpec, CampaignConfig, TaskSpec


class TaskAudioContractTests(unittest.TestCase):
    def test_remote_audio_flag_is_boolean_and_defaults_only_when_absent(self):
        for value in (None, 0, 1, "false", [], {}):
            with self.subTest(value=value), self.assertRaises(minute_api.AuthError):
                minute_api.validate_task_catalog([{"requiresAudio": value}])
        for row in ({}, {"requiresAudio": False}, {"requiresAudio": True}):
            self.assertEqual(minute_api.validate_task_catalog([row]), [row])

    def test_nymeria_audio_requirement_disables_task_with_explanation(self):
        clip = {"clip_uid": "nymeria:fixture:0:60", "source": "nymeria", "dur_s": 60}
        with patch.object(campaign, "_compatible_task_clips", return_value=[clip]), \
             patch.object(campaign, "_catalog_queue_accepts", return_value=True):
            rows = campaign.available_tasks("fixture@example.invalid", "fixture-org",
                remote_tasks=[{"id": "task", "name": "Furniture Assembly", "requiresAudio": True}],
                dataset_provider="nymeria", content_mode="dataset", include_unavailable=True)
        self.assertTrue(rows[0]["requires_audio"])
        self.assertFalse(rows[0]["available_for_duration"])
        self.assertEqual(rows[0]["clip_count"], 0)
        self.assertIn("áudio", rows[0]["unavailable_reason"])

    def test_reviewed_pool_cannot_bypass_audio_requirement(self):
        task = TaskSpec("task", "fixture", 60, 1800, requires_audio=True)
        cfg = CampaignConfig([], [task], candidate_plan={"task": [
            {"clip_uid": "nymeria:fixture:0:60", "source": "nymeria"},
            {"clip_uid": "silent", "source": "ego4d", "has_audio": False},
            {"clip_uid": "unknown", "source": "ego4d"},
            {"clip_uid": "audio", "source": "ego4d", "has_audio": True}]})
        self.assertEqual([c["clip_uid"] for c in campaign.automatic_candidates(task, cfg)],
                         ["unknown", "audio"])

    def test_actual_silent_file_blocks_before_auth_recovery_or_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "silent.mp4"
            subprocess.run([sidecar.ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=s=64x64:r=5:d=1", "-an", "-y", str(path)], check=True)
            self.assertIs(sidecar.probe_video(path)["has_audio"], False)
            with patch.object(campaign.Session, "from_email") as auth, \
                 patch.object(campaign, "pump_pending") as recovery, \
                 patch.object(campaign, "upload_session") as upload:
                result = campaign.upload_to_account({"video_path": str(path), "requires_audio": True,
                    "probe": {"has_audio": True}}, AccountSpec("fixture@example.invalid", "org"),
                    "task", 10, True, True)
            self.assertFalse(result["ok"])
            self.assertFalse(result["retryable"])
            self.assertIn("áudio", result["error"])
            auth.assert_not_called()
            recovery.assert_not_called()
            upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
