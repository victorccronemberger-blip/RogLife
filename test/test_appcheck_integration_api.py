"""Operator diagnostics and recheck contract; no credentials or network."""
from copy import deepcopy
from unittest.mock import patch
import unittest

from moneymin import appcheck
from moneymin.web import server


class AppCheckIntegrationApiTests(unittest.TestCase):
    def setUp(self):
        self.client = server.create_app(for_testing=True).test_client()
        self.status = {"configured": True, "configuration_present": True,
                       "token_available": False, "paused": False,
                       "retry_after_seconds": 0, "diagnostic": None}

    def request(self, before, after=None, *, mint_side_effect=None):
        # status before mint, after live mint, and again if a hard refresh runs.
        after_state = deepcopy(after or before)
        states = [deepcopy(before), after_state, after_state, after_state]
        mint_return = mint_side_effect if mint_side_effect is not None else "private-token-canary"
        with patch("moneymin.roglife_appcheck.ensure_device_defaults_for_writes"), \
             patch.object(appcheck, "app_check_status", side_effect=states), \
             patch.object(appcheck, "mint_app_check_token", return_value=mint_return) as mint:
            result = self.client.post("/api/integrations/appcheck/recheck", json={})
        return result, mint

    def test_status_polling_never_captures_or_renews_expired_tokens(self):
        for left in (None, -10, 0, 30, 1200):
            with self.subTest(left=left):
                state = {**self.status, "device": {"enabled": True, "left_s": left}}
                with patch.object(appcheck, "app_check_status", return_value=state), \
                     patch.object(appcheck, "mint_app_check_token") as mint:
                    response = self.client.get("/api/integrations/appcheck/status")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json(), state)
                mint.assert_not_called()

    def test_explicit_recheck_streams_adb_before_hard_minute_restart(self):
        after = {**self.status, "token_available": True,
                 "device": {"enabled": True, "left_s": 3500, "min_left_s": 600}}
        response, mint = self.request(self.status, after)
        self.assertEqual(response.status_code, 200)
        mint.assert_called_once_with(force_refresh=False, bypass_live_session=True)
        self.assertTrue(response.get_json()["ok"])
        message = response.get_json()["message"]
        self.assertIn("disponível", message)
        self.assertIn("via ADB", message)
        self.assertNotIn("renovado", message)
        self.assertIn("58m", message)
        self.assertIn("depende do Minute", message)
        self.assertNotIn("private-token-canary", response.get_data(as_text=True))

    def test_recheck_hard_refreshes_only_when_live_token_unavailable(self):
        unavailable = {**self.status, "token_available": False,
                       "device": {"enabled": True, "left_s": None}}
        available = {**self.status, "token_available": True,
                     "device": {"enabled": True, "left_s": 3500, "min_left_s": 600}}
        states = [deepcopy(unavailable), deepcopy(unavailable), deepcopy(available)]
        with patch("moneymin.roglife_appcheck.ensure_device_defaults_for_writes") as defaults, \
             patch.object(appcheck, "app_check_status", side_effect=states), \
             patch.object(appcheck, "mint_app_check_token", return_value="private-token-canary") as mint:
            response = self.client.post("/api/integrations/appcheck/recheck", json={})
        defaults.assert_called_once_with()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [(c.kwargs.get("force_refresh"), c.kwargs.get("bypass_live_session"))
             for c in mint.call_args_list],
            [(False, True), (True, True)],
        )
        self.assertTrue(response.get_json()["ok"])
        self.assertIn("após reinício do Minute", response.get_json()["message"])

    def test_existing_token_is_not_reported_as_a_confirmed_renewal(self):
        state = {**self.status, "token_available": True,
                 "device": {"enabled": True, "left_s": 1800, "min_left_s": 600}}
        with patch("moneymin.roglife_appcheck.ensure_device_defaults_for_writes"), \
             patch.object(appcheck, "app_check_status", return_value=state), \
             patch.object(appcheck, "mint_app_check_token", return_value=None) as mint:
            response = self.client.post("/api/integrations/appcheck/recheck", json={})
        self.assertEqual(response.status_code, 200)
        mint.assert_called_once_with(force_refresh=False, bypass_live_session=True)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertIn("disponível (30m 0s restantes via ADB)", body["message"])
        self.assertNotIn("renovado", body["message"])
        self.assertEqual(body["appcheck"], state)

    def test_cooldown_pause_and_incomplete_config_never_mint(self):
        for overrides in ({"retry_after_seconds": 3600}, {"paused": True},
                          {"configured": False}, {"configured": False, "configuration_present": False}):
            with self.subTest(overrides=overrides):
                response, mint = self.request({**self.status, **overrides})
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.get_json()["ok"])
                mint.assert_not_called()

    def test_rejection_preserves_sanitized_firebase_diagnostic(self):
        after = {**self.status, "retry_after_seconds": 3600,
                 "diagnostic": {"category": "permission_denied", "http_status": 403,
                                "firebase_status": "PERMISSION_DENIED",
                                "message": "Firebase negou permissão na troca de App Check."}}
        # Live mint leaves token unavailable, so recheck also attempts hard refresh.
        states = [deepcopy(self.status), deepcopy(after), deepcopy(after)]
        with patch("moneymin.roglife_appcheck.ensure_device_defaults_for_writes"), \
             patch.object(appcheck, "app_check_status", side_effect=states), \
             patch.object(appcheck, "mint_app_check_token", return_value="private-token-canary") as mint:
            response = self.client.post("/api/integrations/appcheck/recheck", json={})
        self.assertEqual(mint.call_count, 2)
        self.assertFalse(response.get_json()["ok"])
        self.assertEqual(response.get_json()["appcheck"]["diagnostic"]["http_status"], 403)

    def test_body_cannot_supply_or_replace_secret(self):
        with patch.object(appcheck, "mint_app_check_token") as mint:
            for body in ({"debugToken": "private-token-canary"}, [], None):
                with self.subTest(body_type=type(body).__name__):
                    result = self.client.post("/api/integrations/appcheck/recheck", json=body)
                    self.assertEqual(result.status_code, 400)
                    self.assertNotIn("private-token-canary", result.get_data(as_text=True))
            mint.assert_not_called()

    def test_get_integrations_reads_diagnostic_without_mint(self):
        with patch.object(server, "_migrate_legacy_integrations", return_value={}), \
             patch.object(server, "_legacy_aws_credentials", return_value={}), \
             patch.object(server, "_storage_snapshot", return_value={}), \
             patch.object(server.ego4d, "has_timed_narrations", return_value=False), \
             patch.object(server.readiness, "_binary_works", return_value=False), \
             patch.object(server.readiness, "_private_browser_present", return_value=False), \
             patch.object(appcheck, "app_check_status", return_value=self.status), \
             patch.object(appcheck, "mint_app_check_token") as mint:
            response = self.client.get("/api/integrations")
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["appcheck"], self.status)
        mint.assert_not_called()
