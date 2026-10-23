"""Error boundaries exercised offline, including stdout and persisted JSON."""
import io
import json
import unittest
from contextlib import nullcontext, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_campaign_end_to_end as fixtures
from test_account_delivery_typed_failures import deliver_failure
from moneymin.campaigns import account_delivery
from moneymin.campaigns.execution import sanitize_account_error
from moneymin.minute_api import AuthError
from moneymin.upload import UploadError


SECRETS = (
    'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmaXh0dXJlIn0.fakeJWTsignature',
    'password=fixture-private-password',
    'https://fixture.blob.core.windows.net/media?sv=2026&sig=fixture-private-sas',
)
POISON = ' remote body '.join(SECRETS)


def deliver_chunk(account, *, typed=True):
    chunk = SimpleNamespace(
        state='failed', upload_id='fixture-upload', error=POISON,
        evaluate_result=None, authentication_required=False, review_required=True,
        blocked_reason='app_check' if typed else None,
        appcheck_cause='device_unavailable' if typed else POISON,
        appcheck_message=POISON, phase='complete' if typed else POISON,
        account_issue_code='app_check' if typed else POISON,
        http_status=429 if typed else POISON, request_not_sent=False)
    services = Mock(AuthError=AuthError, UploadError=UploadError, Path=Path)
    services.org_policy.account_kind.return_value = 'other'
    services.account_registration_route.side_effect = lambda email: nullcontext()
    services.device_profile.recorded_at_to_wall_ms.return_value = None
    services.time.time.return_value = 1
    services.list_sidecars.return_value = []
    services._reconcile_uploads.return_value = None
    services._terminal_retry_parent.return_value = None
    services._new_identity.return_value = ('fixture-session', 'fixture-log', 'fixture-time')
    services._chunk_plan.return_value = [(0, 60000)]
    services._build_sidecar.return_value = b'inert sidecar fixture'
    services.upload_session.return_value = SimpleNamespace(
        session_id='fixture-session', finalized=False, finalize_status=429,
        chunks=[chunk], authentication_required=False, review_required=True)
    with patch('moneymin.appcheck.ensure_app_check_fresh',
               return_value={'token_available': True}), patch(
               'moneymin.mobile.physical_binding.device_mode_requires_physical', return_value=False):
        result = account_delivery.upload_to_account(
            services, {'duration_ms': 60000, 'video_path': 'fixture.mp4',
                       'probe': {'fps': 30}, 'imu_csv': 'inert',
                       'clip_uid': 'fixture', 'registry_key': 'fixture'},
            account, 'task', 30, True, True, session=Mock(_live=True), recover_pending=False)
    services.upload_session.assert_called_once()
    return result


class CampaignErrorSecretSafetyTests(unittest.TestCase):
    def assert_safe(self, value):
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        for secret in SECRETS:
            self.assertNotIn(secret, text)

    def test_typed_results_keep_cause_and_policy_without_exception_text(self):
        for code in ('restricted', 'authentication', 'app_check', 'dns', 'timeout'):
            error = AuthError(POISON, code=code, http_status=403)
            error.appcheck_cause = POISON
            error.appcheck_message = POISON
            with self.subTest(code=code):
                result = deliver_failure(error)
                self.assert_safe(result)
                self.assertEqual(result['account_issue_code'], code)
                self.assertEqual(result['restriction_confirmed'], code == 'restricted')
                self.assertEqual(result['authentication_required'], code in {'authentication', 'app_check'})
                self.assertEqual(result['retryable'], code == 'timeout')
        error = UploadError(POISON, phase='complete', status_code=503,
                            review_required=True, appcheck_message=POISON)
        result = deliver_failure(error)
        self.assert_safe(result)
        self.assertIn('complete', result['error'])
        self.assertEqual(result['http_status'], 503)
        self.assertTrue(result['review_required'])
        self.assertFalse(result['retryable'])

    def test_real_chunk_boundary_preserves_typed_facts_and_legacy_fallback(self):
        for typed in (False, True):
            with self.subTest(typed=typed):
                result = deliver_chunk(SimpleNamespace(email='fixture@example.invalid', org_key='org'), typed=typed)
                self.assert_safe(result)
                self.assertTrue(result['review_required'])
                self.assertFalse(result['retryable'])
                self.assertFalse(result.get('restriction_confirmed', False))
                if typed:
                    self.assertEqual(result['account_issue_code'], 'app_check')
                    self.assertEqual(result['phase'], 'complete')
                    self.assertEqual(result['http_status'], 429)
                    self.assertEqual(result['appcheck_cause'], 'device_unavailable')
                else:
                    self.assertNotIn('account_issue_code', result)
                    self.assertNotIn('phase', result)

    def test_stdout_report_and_events_never_receive_secret_errors(self):
        for mode in ('auth', 'upload', 'chunk', 'legacy_chunk', 'legacy_result'):
            with self.subTest(mode=mode):
                fixture = fixtures.CampaignEndToEndTests('test_success_matches_polling_and_persisted_history')
                fixture.setUp()
                results = []
                try:
                    def send(item, account, *args, **kwargs):
                        if mode == 'auth':
                            result = deliver_failure(AuthError(POISON, code='access_paused', http_status=403), account)
                        elif mode == 'upload':
                            result = deliver_failure(UploadError(
                                POISON, phase='complete', status_code=503, transient=False,
                                appcheck_message=POISON), account)
                        elif mode in {'chunk', 'legacy_chunk'}:
                            result = deliver_chunk(account, typed=mode == 'chunk')
                        else:
                            result = {'email': account.email, 'ok': False, 'error': POISON,
                                      'retryable': False, 'appcheck_message': POISON}
                        results.append(result)
                        return result
                    fixture.send.side_effect = send
                    stdout = io.StringIO()
                    with redirect_stdout(stdout):
                        response = fixture.client.post('/api/campaigns', json=fixture.body)
                        self.assertEqual(response.status_code, 200, response.get_json())
                        snapshot, report = fixture.finish()
                    self.assertTrue(results)
                    for value in (stdout.getvalue(), snapshot, report, results):
                        self.assert_safe(value)
                    attempts = report['items'][0]['accounts']
                    self.assertTrue(attempts)
                    self.assertTrue(any(e['kind'] == 'account_done' for e in snapshot['events']))
                    for attempt in attempts:
                        self.assertFalse(attempt['ok'])
                        if mode == 'auth':
                            self.assertEqual(attempt['account_issue_code'], 'access_paused')
                        elif mode == 'upload':
                            self.assertEqual(attempt['phase'], 'complete')
                            self.assertEqual(attempt['http_status'], 503)
                        elif mode == 'chunk':
                            self.assertEqual(attempt['account_issue_code'], 'app_check')
                            self.assertTrue(attempt['review_required'])
                    fixture.mark.assert_not_called()
                finally:
                    fixture.instance.stop()
                    if fixture.instance._thread:
                        fixture.instance._thread.join(5)
                    fixture.doCleanups()

    def test_unknown_metadata_is_not_a_safe_message_channel(self):
        result = {'error': POISON, 'account_issue_code': POISON, 'phase': POISON,
                  'http_status': POISON, 'appcheck_message': POISON, 'appcheck_cause': POISON,
                  'authentication_required': True, 'review_required': True, 'retryable': False}
        sanitize_account_error(result)
        self.assert_safe(result)
        self.assertTrue(result['authentication_required'])
        self.assertTrue(result['review_required'])
        self.assertFalse(result['retryable'])

    def test_typed_prepare_failure_is_safe_before_logging_and_issue_persistence(self):
        fixture = fixtures.CampaignEndToEndTests('test_success_matches_polling_and_persisted_history')
        fixture.setUp()
        try:
            fixture.prepare.side_effect = UploadError(
                POISON, phase='prepare', transient=False, appcheck_message=POISON)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                response = fixture.client.post('/api/campaigns', json=fixture.body)
                self.assertEqual(response.status_code, 200, response.get_json())
                snapshot, report = fixture.finish()
            for value in (stdout.getvalue(), snapshot, report):
                self.assert_safe(value)
            issue = next(i for i in report['issues'] if i['kind'] == 'clip_prepare_done')
            self.assertIn('prepare', issue['error'])
            fixture.send.assert_not_called()
            fixture.mark.assert_not_called()
        finally:
            fixture.instance.stop()
            if fixture.instance._thread:
                fixture.instance._thread.join(5)
            fixture.doCleanups()
