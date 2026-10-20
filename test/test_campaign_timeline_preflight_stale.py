"""F06 timeline stages and F07 stale preflight callback contract (Python side)."""
from __future__ import annotations

import unittest
from pathlib import Path

from moneymin.web import runner


class TimelineStagesF06(unittest.TestCase):
    def test_account_start_is_operation_not_transfer(self):
        card = runner._public_event("account_start", {"email": "a@example.invalid"})
        self.assertEqual(card["stage"], "Operação")
        self.assertEqual(card["title"], "Operação iniciada")

    def test_runner_maps_progress_phases_to_distinct_stages(self):
        cases = [
            ("appcheck", "Validação"),
            ("encode", "Pacote"),
            ("sidecar", "Pacote"),
            ("create", "Transferência"),
            ("sas", "Transferência"),
            ("transport", "Transferência"),
            ("complete", "Confirmação"),
            ("evaluate", "Confirmação"),
            ("finalize", "Confirmação"),
        ]
        for phase, stage in cases:
            with self.subTest(phase=phase):
                campaign_runner = runner.CampaignRunner()
                campaign_runner._on_event(
                    "account_progress",
                    {"email": "a@example.invalid", "phase": phase, "state": "running", "attempt": 1},
                )
                self.assertEqual(campaign_runner.stage, stage)
                self.assertNotIn("token", campaign_runner.current.casefold())
                self.assertNotIn("http", campaign_runner.current.casefold())


class StalePreflightContractF07(unittest.TestCase):
    def test_generation_and_closing_gate_is_in_campaign_preflight_callback(self):
        text = (Path(__file__).resolve().parents[1]
                / "desktop/src/MainWindowCampaigns.cpp").read_text(encoding="utf-8")
        self.assertIn("preflightGeneration", text)
        self.assertIn(
            "_closing || preflightGeneration != _operationBackendGeneration",
            text,
        )


if __name__ == "__main__":
    unittest.main()
