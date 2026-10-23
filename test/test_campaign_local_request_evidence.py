"""A local block must not hide another chunk's uncertain remote operation."""
import io
import json
from contextlib import redirect_stdout
from copy import deepcopy
import unittest
from unittest.mock import patch

from moneymin import appcheck, campaign
from moneymin.upload_types import ChunkResult, UploadResult
import test_campaign_terminal_replacement as fixtures


class CampaignLocalRequestEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CampaignTerminalReplacementTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.stack.enter_context(patch.object(
            appcheck, "ensure_app_check_fresh",
            return_value={"token_available": True, "configured": True}))

    def test_each_receiptless_attempt_requires_its_own_local_evidence(self):
        sensitive = "Diagnóstico local de fixture password=fixture-private-password"
        for markers, review in (
            ([True], False), ([False], True), ([True, False], True),
            (["true"], True), ([1], True),
        ):
            with self.subTest(markers=markers):
                chunks = [ChunkResult(
                    upload_id="", chunk_index=index, log_id=f"fixture-{index}",
                    blob_path="", size_bytes=0, duration_ms=300_000,
                    state="failed", error="App Check indisponível",
                    authentication_required=True, blocked_reason="app_check",
                    appcheck_cause="not_configured",
                    appcheck_message=sensitive,
                    request_not_sent=marker,
                ) for index, marker in enumerate(markers)]
                delivery = UploadResult(
                    session_id="fixture-session", org_key="fixture-org",
                    task_id="fixture-task", chunks=chunks)
                original_chunks = deepcopy(chunks)
                original_item = deepcopy(self.fixture.item)
                stdout = io.StringIO()
                with patch.object(campaign, "upload_session", return_value=delivery) as send, redirect_stdout(stdout):
                    result = campaign.upload_to_account(
                        self.fixture.item, self.fixture.account, "fixture-task",
                        30, True, True, session=self.fixture.session,
                        recover_pending=False)
                send.assert_called_once()
                self.assertFalse(result["ok"], result)
                self.assertTrue(result["authentication_required"], result)
                self.assertFalse(result["retryable"], result)
                self.assertEqual(result.get("review_required", False), review, result)
                self.assertEqual(result["appcheck_cause"], "not_configured")
                self.assertEqual(result["blocked_reason"], "app_check")
                self.assertEqual(result["error"], "App Check indisponível; regularize o acesso antes de retomar.")
                self.assertFalse(result["finalized"])
                self.assertEqual(result["session_id"], "fixture-session")
                self.assertEqual(result["uploads"], [""] * len(markers))
                # One chunk's local proof cannot become proof for the whole session.
                self.assertNotIn("request_not_sent", result)
                self.assertNotIn("appcheck_message", result)
                self.assertEqual(chunks, original_chunks)
                self.assertEqual(self.fixture.item, original_item)
                for text in (json.dumps(result), stdout.getvalue()):
                    self.assertNotIn("Diagnóstico local de fixture", text)
                    self.assertNotIn("fixture-private-password", text)


if __name__ == "__main__":
    unittest.main()
