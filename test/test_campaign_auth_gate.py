import unittest
from unittest.mock import Mock, patch

from moneymin import campaign
from moneymin.campaign_types import AccountSpec
from moneymin.minute_api import AuthError
from moneymin.upload_types import UploadError


class CampaignAuthGateTests(unittest.TestCase):
    def _access_failure(self, error):
        session = Mock(_live=True)
        session.ensure_auth.side_effect = error
        with patch.object(campaign.Session, "from_email", return_value=session), \
             patch.object(campaign.org_policy, "account_kind", return_value="other"), \
             patch.object(campaign, "upload_session") as upload:
            result = campaign.upload_to_account({}, AccountSpec("a@example.invalid", "org"),
                                                "task", 30, True, True)
        upload.assert_not_called()
        return result

    def test_authentication_evidence_survives_account_boundary(self):
        for error in (
            AuthError("Acesso expirado", code="authentication", http_status=401),
            AuthError("App Check recusado", code="app_check", http_status=429),
            UploadError("Recusado", status_code=429, blocked_reason="APP_CHECK"),
            UploadError("Acesso expirado", status_code=401),
        ):
            with self.subTest(error=type(error).__name__, status=getattr(error, "status_code", None)):
                result = self._access_failure(error)
                self.assertTrue(result["authentication_required"])
                self.assertFalse(result["retryable"])
                self.assertFalse(result["restriction_confirmed"])
                self.assertIn(result["http_status"], (401, 429))
        self.assertEqual(result["http_status"], 401)

    def test_uncertain_access_operation_is_never_retried_as_network_failure(self):
        error = AuthError("Resposta ausente", code="network", http_status=-1)
        error.remote_effect_possible = True
        result = self._access_failure(error)
        self.assertTrue(result["review_required"])
        self.assertFalse(result["retryable"])
        self.assertFalse(result["authentication_required"])
        self.assertEqual(result["account_issue_code"], "network")

    def test_local_app_check_capture_failure_is_not_replayed_even_if_transient(self):
        result = self._access_failure(UploadError(
            "App Check indisponível", transient=True, phase="prepare",
            blocked_reason="app_check", appcheck_cause="device_capture_failed",
            appcheck_message="Fixture: credential unavailable", request_not_sent=True))
        self.assertTrue(result["authentication_required"])
        self.assertTrue(result["request_not_sent"])
        self.assertFalse(result["retryable"])
        self.assertFalse(result["review_required"])
        self.assertFalse(result["restriction_confirmed"])
        self.assertEqual(result["appcheck_cause"], "device_capture_failed")

    def test_policy_and_transient_failures_are_not_authentication_expiry(self):
        for code, status, retryable in (("restricted", 403, False), ("forbidden", 403, False),
                                       ("service", 503, True), ("rate_limit", 429, True)):
            with self.subTest(code=code):
                result = self._access_failure(AuthError("Recusado", code=code, http_status=status))
                self.assertFalse(result["authentication_required"])
                self.assertEqual(result["retryable"], retryable)
                self.assertEqual(result["restriction_confirmed"], code == "restricted")

    def test_unknown_header_value_is_not_exported_as_protocol_evidence(self):
        result = self._access_failure(UploadError("Recusado", status_code=403,
                                                blocked_reason="private-fixture-header"))
        self.assertNotIn("blocked_reason", result)
        self.assertFalse(result["authentication_required"])

    def test_ego4d_without_confirmed_real_sensors_never_opens_session(self):
        for flag in (None, False, 'true', 1):
            with self.subTest(flag=flag), patch.object(campaign.Session, 'from_email') as session, \
                 patch.object(campaign, 'upload_session') as upload:
                result = campaign.upload_to_account({'source': 'ego4d', 'imu_real': flag},
                    AccountSpec('fixture@example.invalid', 'org'), 'task', 30, True, True)
                self.assertFalse(result['ok'])
                self.assertFalse(result['finalized'])
                self.assertFalse(result['retryable'])
                session.assert_not_called()
                upload.assert_not_called()

    def test_missing_ego4d_csv_cannot_fall_back_to_synthetic_sensor_data(self):
        session = Mock(_live=True)
        session.recording_policy.limits.return_value = {'min_duration_ms': 60000, 'max_duration_ms': 1800000}
        item = {'source': 'ego4d', 'imu_real': True, 'duration_ms': 60000,
                'video_path': 'fixture-only.mp4', 'clip_uid': 'fixture',
                'probe': {'duration_ms': 60000, 'fps': 30}}
        with patch.object(campaign.org_policy, 'account_kind', return_value='other'), \
             patch.object(campaign.device_profile, 'get_profile', return_value=Mock()), \
             patch.object(campaign, 'build_imu_csv') as synthetic, \
             patch.object(campaign, 'upload_session') as upload:
            result = campaign.upload_to_account(item, AccountSpec('fixture@example.invalid', 'org'),
                'task', 30, True, True, session=session, recover_pending=False)
        self.assertFalse(result['ok'])
        self.assertFalse(result['retryable'])
        self.assertIn('sem substituição sintética', result['error'])
        synthetic.assert_not_called()
        upload.assert_not_called()

    def test_refreshed_firebase_token_does_not_skip_minute_access_validation(self):
        session = Mock(_live=True)
        session.ensure_auth.side_effect = AuthError("Conta desativada", code="restricted")
        cache = {}
        account = AccountSpec("account@example.invalid", "org")
        with patch.object(campaign.Session, "from_email", return_value=session), \
             patch.object(campaign.org_policy, "account_kind", return_value="other"), \
             patch.object(campaign, "upload_session") as upload:
            result = campaign.upload_to_account({}, account, "task", 30, True, True,
                                                session_cache=cache)
        session.ensure_auth.assert_called_once_with(org_key="org")
        upload.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertTrue(result["restriction_confirmed"])
        self.assertFalse(result["retryable"])
        self.assertEqual(cache, {})

    def test_temporary_access_failure_preserves_cause_without_caching_session(self):
        session = Mock(_live=True)
        session.ensure_auth.side_effect = AuthError("Consulta indisponível", code="service")
        cache = {}
        with patch.object(campaign.Session, "from_email", return_value=session), \
             patch.object(campaign.org_policy, "account_kind", return_value="other"):
            result = campaign.upload_to_account({}, AccountSpec("a@example.invalid", "org"),
                                                "task", 30, True, True, session_cache=cache)
        self.assertTrue(result["retryable"])
        self.assertFalse(result["restriction_confirmed"])
        self.assertEqual(cache, {})
