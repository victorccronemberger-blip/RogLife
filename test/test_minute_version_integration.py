"""Installed-version integration without Android, remote writes, or real accounts."""
from contextlib import ExitStack
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, PropertyMock, patch

from moneymin import config, minute_api, roglife_appcheck
from moneymin.mobile import minute_version, physical_binding
from moneymin.roglife_appcheck import provider


class InstalledVersionIntegrationTests(TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(TemporaryDirectory(prefix="qmoney-live-version-")))
        self.stack.enter_context(patch.object(config, "DATA_DIR", self.root))
        self.stack.enter_context(patch.object(config, "APP_VERSION", "1.29.0"))
        self.stack.enter_context(patch.object(minute_api, "_VERSION_GATE_MEMORY", {}))
        self.stack.enter_context(patch.object(provider, "device_mode_enabled", return_value=True))
        self.stack.enter_context(patch.object(roglife_appcheck, "device_mode_enabled", return_value=True))
        self.installed = {
            "serial": "FIXTURE-PHONE", "package": "com.bakerdata.minute",
            "version_name": "1.30.0", "version_code": 1004039,
        }
        self.observe = self.stack.enter_context(patch.object(
            minute_version, "observe_installed_version", side_effect=lambda **kw: dict(self.installed)))
        self.identity = {
            "serial": "FIXTURE-PHONE", "device_id": "android.ssaid:inert-fixture",
            "model": "SM-S901E", "sdk_int": 36, "uptime_ns": 10_000_000_000,
            "app_version": "1.29.0", "app_version_code": "1004038",
        }
        self.stack.enter_context(patch.object(provider, "physical_identity", return_value=self.identity))
        self.network = self.stack.enter_context(patch.object(
            minute_api, "_request", side_effect=AssertionError("external network forbidden")))
        self.detailed = self.stack.enter_context(patch.object(
            minute_api, "_request_detailed", side_effect=AssertionError("external network forbidden")))
        self.latch = self.root / "version-gate.json"

    def seed_minimum(self, version="1.30.0"):
        self.latch.write_text(json.dumps({
            "minVersion": version, "appVersion": "1.29.0", "latchedAt": 123}), encoding="utf-8")

    def test_installed_130_meets_minimum_without_editing_baseline_or_erasing_latch(self):
        self.seed_minimum()
        before = self.latch.read_bytes()
        minute_api.require_supported_client(force_refresh=True)
        self.assertEqual(config.APP_VERSION, "1.29.0")
        self.assertFalse(minute_api._version_gate_blocks())
        self.assertEqual(self.latch.read_bytes(), before)
        self.assertTrue(any(call.kwargs.get("force_refresh") is True for call in self.observe.call_args_list))
        self.network.assert_not_called()
        self.detailed.assert_not_called()

    def test_phone_update_is_detected_in_same_process_without_clearing_latch(self):
        self.seed_minimum()
        self.installed.update(version_name="1.29.0", version_code=1004038)
        with self.assertRaises(minute_api.AuthError) as caught:
            minute_api.require_supported_client(force_refresh=True)
        self.assertEqual(caught.exception.account_issue_code, "version")
        self.assertIn("1.29.0", str(caught.exception))
        self.assertIn("1.30.0", str(caught.exception))
        self.installed.update(version_name="1.30.0", version_code=1004039)
        minute_api.require_supported_client(force_refresh=True)
        self.assertTrue(self.latch.exists())

    def test_installed_version_not_baseline_controls_known_server_minimum(self):
        self.seed_minimum("1.31.0")
        with patch.object(config, "APP_VERSION", "99.0.0"):
            self.assertTrue(minute_api._version_gate_blocks())
            with self.assertRaises(minute_api.AuthError):
                minute_api.require_supported_client()

    def test_future_version_or_changed_build_is_never_automatically_promoted(self):
        for version, code in (("1.31.0", 1004040), ("1.30.0", 1004040)):
            with self.subTest(version=version, code=code):
                self.installed.update(version_name=version, version_code=code)
                with self.assertRaises(minute_api.AuthError) as caught:
                    minute_api.require_supported_client()
                self.assertEqual(caught.exception.account_issue_code, "version")
                self.assertIn("não revisado", str(caught.exception))
        self.network.assert_not_called()
        self.detailed.assert_not_called()

    def test_version_refresh_between_guard_observations_cannot_admit_unknown_build(self):
        self.seed_minimum()
        self.observe.side_effect = [dict(self.installed), {
            **self.installed, "version_name": "1.31.0", "version_code": 1004040}]
        with self.assertRaises(minute_api.AuthError) as caught:
            minute_api.require_supported_client(force_refresh=True)
        self.assertEqual(caught.exception.account_issue_code, "version")
        self.assertIn("não revisado", str(caught.exception))
        self.network.assert_not_called()
        self.detailed.assert_not_called()

    def test_missing_installed_observation_never_falls_back_to_static_version(self):
        self.observe.side_effect = minute_version.MinuteVersionError("device_unavailable")
        with self.assertRaises(minute_api.AuthError) as caught:
            minute_api.require_supported_client()
        self.assertEqual(caught.exception.account_issue_code, "version")
        self.network.assert_not_called()
        self.detailed.assert_not_called()

    def test_binding_refreshes_stale_cached_version_without_mutating_identity(self):
        bound = physical_binding.require_campaign_physical_binding()
        self.assertEqual(bound["app_version"], "1.30.0")
        self.assertEqual(bound["opened"]["app_version"], "1.30.0")
        self.assertEqual(bound["identity"]["app_version_code"], "1004039")
        self.assertEqual(self.identity["app_version"], "1.29.0")
        self.assertEqual(self.identity["app_version_code"], "1004038")

    def test_another_phone_cannot_reuse_previous_identity_with_new_version(self):
        self.installed["serial"] = "SECOND-FIXTURE-PHONE"
        with self.assertRaises(physical_binding.PhysicalDeviceRequiredError) as caught:
            physical_binding.require_campaign_physical_binding()
        self.assertEqual(caught.exception.cause, "physical_identity_device_changed")

    def test_binding_refuses_failed_observation_instead_of_reusing_stale_version(self):
        self.observe.side_effect = minute_version.MinuteVersionError("adb_timeout")
        with self.assertRaises(physical_binding.PhysicalDeviceRequiredError) as caught:
            physical_binding.require_campaign_physical_binding()
        self.assertEqual(caught.exception.cause, "physical_app_version_unavailable")

    def test_diagnostic_headers_follow_installed_version_not_static_config(self):
        headers = {"X-App-Version": "1.29.0"}
        minute_api._align_physical_device_headers(headers)
        self.assertEqual(headers["X-App-Version"], "1.30.0")

    def test_latch_records_actual_observed_version_instead_of_old_baseline(self):
        minute_api._maybe_latch_version_gate(json.dumps({"detail": {
            "error": "app_version_too_old", "min_version": "1.31.0"}}))
        state = json.loads(self.latch.read_text(encoding="utf-8"))
        self.assertEqual(state["appVersion"], "1.30.0")
        self.assertEqual(state["minVersion"], "1.31.0")

    def test_failed_observation_preserves_durable_receipt_and_pending_veto(self):
        self.seed_minimum()
        before = self.latch.read_bytes()
        self.observe.side_effect = minute_version.MinuteVersionError("device_unavailable")
        minute_api._maybe_latch_version_gate(json.dumps({"detail": {
            "error": "app_version_too_old", "min_version": "1.31.0"}}))
        self.assertEqual(self.latch.read_bytes(), before)
        state = minute_api._version_gate_state()
        self.assertEqual(state["minVersion"], "1.31.0")
        self.assertTrue(state["persistence_pending"])
        with self.assertRaises(minute_api.AuthError):
            minute_api.require_supported_client()

    def test_recovered_observation_can_persist_the_same_pending_minimum(self):
        self.seed_minimum()
        denial = json.dumps({"detail": {"error": "app_version_too_old", "min_version": "1.31.0"}})
        self.observe.side_effect = minute_version.MinuteVersionError("device_unavailable")
        minute_api._maybe_latch_version_gate(denial)
        self.assertTrue(minute_api._version_gate_state()["persistence_pending"])
        self.observe.side_effect = lambda **kw: dict(self.installed)
        minute_api._maybe_latch_version_gate(denial)
        state = json.loads(self.latch.read_text(encoding="utf-8"))
        self.assertEqual(state["minVersion"], "1.31.0")
        self.assertEqual(state["appVersion"], "1.30.0")
        self.assertNotIn("persistence_pending", minute_api._version_gate_state())

    def test_recovered_observation_never_downgrades_an_older_pending_minimum(self):
        self.seed_minimum()
        self.observe.side_effect = minute_version.MinuteVersionError("device_unavailable")
        minute_api._maybe_latch_version_gate(json.dumps({"detail": {
            "error": "app_version_too_old", "min_version": "1.31.0"}}))
        self.observe.side_effect = lambda **kw: dict(self.installed)
        minute_api._maybe_latch_version_gate(json.dumps({"detail": {
            "error": "app_version_too_old", "min_version": "1.30.0"}}))
        state = json.loads(self.latch.read_text(encoding="utf-8"))
        self.assertEqual(state["minVersion"], "1.31.0")
        self.assertEqual(state["appVersion"], "1.30.0")

    def test_explicit_clear_requires_actual_supported_phone_version(self):
        self.seed_minimum()
        self.installed.update(version_name="1.29.0", version_code=1004038)
        with patch.object(config, "APP_VERSION", "99.0.0"):
            with self.assertRaises(minute_api.AuthError):
                minute_api._clear_version_gate()
        self.assertTrue(self.latch.exists())
        self.installed.update(version_name="1.30.0", version_code=1004039)
        minute_api._clear_version_gate()
        self.assertFalse(self.latch.exists())

    def test_push_token_body_uses_same_installed_version_as_headers(self):
        session = minute_api.Session({"email": "fixture@example.invalid", "idToken": "inert"})
        with patch.object(session, "request_detailed", return_value=minute_api.HttpResponse(204, "", {})) as request:
            session.register_push_token("inert-push-token")
        self.assertEqual(request.call_args.kwargs["body"]["appVersion"], "1.30.0")

    def test_update_after_push_body_is_materialized_stops_before_dispatch(self):
        reads = 0

        def installed(**kwargs):
            nonlocal reads
            reads += 1
            if reads == 1:
                return {**self.installed, "version_name": "1.29.0", "version_code": 1004038}
            return dict(self.installed)

        self.observe.side_effect = installed
        session = minute_api.Session({"email": "fixture@example.invalid", "idToken": "inert"})
        profile = Mock()
        profile.headers.return_value = {}
        with patch.object(minute_api.Session, "id_token", new_callable=PropertyMock, return_value="inert"), \
                patch.object(minute_api, "require_app_check_for_write", return_value={}), \
                patch.object(session, "_check_local_restrictions"), \
                patch.object(minute_api.device_profile, "get_profile", return_value=profile):
            with self.assertRaises(minute_api.AuthError) as caught:
                session.register_push_token("inert-push-token")
        self.assertEqual(caught.exception.account_issue_code, "version")
        self.assertFalse(caught.exception.remote_effect_possible)
        self.network.assert_not_called()
        self.detailed.assert_not_called()

    def test_phone_update_during_auth_refresh_never_resends_stale_push_headers(self):
        for detailed in (False, True):
            with self.subTest(detailed=detailed), ExitStack() as stack:
                self.installed.update(version_name="1.29.0", version_code=1004038)
                session = minute_api.Session({"email": "fixture@example.invalid", "idToken": "inert"})
                profile = Mock()
                profile.headers.return_value = {}
                stack.enter_context(patch.object(
                    minute_api.Session, "id_token", new_callable=PropertyMock, return_value="inert"))
                stack.enter_context(patch.object(minute_api, "require_app_check_for_write", return_value={}))
                stack.enter_context(patch.object(session, "_check_local_restrictions"))
                stack.enter_context(patch.object(minute_api.device_profile, "get_profile", return_value=profile))
                transport = stack.enter_context(patch.object(
                    minute_api, "_request_detailed" if detailed else "_request",
                    return_value=minute_api.HttpResponse(401, "Unauthorized", {}) if detailed
                    else (401, "Unauthorized")))
                stack.enter_context(patch.object(session, "refresh", side_effect=lambda: self.installed.update(
                    version_name="1.30.0", version_code=1004039)))
                request = session.request_detailed if detailed else session.request
                with self.assertRaises(minute_api.AuthError) as caught:
                    request("PUT", "/api/v1/devices/push-token", {
                        "token": "inert-push-token", "locale": "pt", "appVersion": "1.29.0"})
                self.assertEqual(caught.exception.account_issue_code, "version")
                self.assertTrue(caught.exception.remote_effect_possible)
                transport.assert_called_once()

    def test_unknown_version_does_not_block_diagnostic_get_but_stops_writes(self):
        self.installed.update(version_name="1.31.0", version_code=1004040)
        session = minute_api.Session({"email": "fixture@example.invalid", "idToken": "inert"})
        profile = Mock()
        profile.headers.return_value = {}
        self.detailed.side_effect = None
        self.detailed.return_value = minute_api.HttpResponse(200, "{}", {})
        with patch.object(minute_api.Session, "id_token", new_callable=PropertyMock, return_value="inert"), \
                patch.object(minute_api, "require_app_check_for_write", return_value={}), \
                patch.object(session, "_check_local_restrictions"), \
                patch.object(minute_api.device_profile, "get_profile", return_value=profile):
            response = session.request_detailed("GET", "/api/v1/orgs/fixture/quota")
            self.assertEqual(response.status, 200)
            self.assertEqual(self.detailed.call_args.kwargs["headers"]["X-App-Version"], "1.31.0")
            with self.assertRaises(minute_api.AuthError):
                session.request_detailed("POST", "/api/v1/uploads?org_key=fixture", {})
        self.detailed.assert_called_once()
