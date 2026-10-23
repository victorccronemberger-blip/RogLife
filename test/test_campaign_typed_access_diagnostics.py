"""Real engine/report/runner chain; prepared media and accounts are inert fixtures."""
import json
import time
import unittest
from unittest.mock import Mock, patch

import test_campaign_end_to_end as fixtures
from moneymin import campaign
from moneymin.minute_api import AuthError
from moneymin.campaigns.execution import safe_account_diagnostics
from moneymin.web import runner


class TypedAccessDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CampaignEndToEndTests(
            'test_success_matches_polling_and_persisted_history')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.stop_worker)

    def stop_worker(self):
        self.fixture.instance.stop()
        worker = self.fixture.instance._thread
        if worker:
            worker.join(5)

    def start(self, result):
        self.fixture.send.side_effect = lambda item, account, *a, **k: {
            'email': account.email, 'ok': False, 'retryable': False, **result}
        response = self.fixture.client.post('/api/campaigns', json=self.fixture.body)
        self.assertEqual(response.status_code, 200, response.get_json())

    def test_real_delivery_to_report_and_public_event_preserves_code(self):
        def send(item, account, *a, **kw):
            session = Mock(_live=True)
            session.ensure_auth.side_effect = AuthError(
                'timeout quality authentication Bearer secret-fixture',
                code='access_paused', http_status=403)
            with patch.object(campaign.Session, 'from_email', return_value=session), \
                 patch.object(campaign.org_policy, 'account_kind', return_value='other'), \
                 patch.object(campaign, 'upload_session') as upload:
                result = fixtures.REAL_UPLOAD_TO_ACCOUNT(
                    item, account, 'task', 30, True, True)
                upload.assert_not_called()
                return result
        self.fixture.send.side_effect = send
        response = self.fixture.client.post('/api/campaigns', json=self.fixture.body)
        self.assertEqual(response.status_code, 200, response.get_json())
        snapshot, report = self.fixture.finish()
        attempt = report['items'][0]['accounts'][0]
        self.assertEqual(attempt['account_issue_code'], 'access_paused')
        self.assertEqual(attempt['http_status'], 403)
        events = [e for e in snapshot['events'] if e['kind'] == 'account_done']
        self.assertTrue(events, snapshot)
        self.assertEqual(events[0]['account_issue_code'], 'access_paused')
        self.assertIn('acesso está pausado', events[0]['detail'])
        self.assertNotIn('secret-fixture', json.dumps(snapshot))

    def test_pause_report_keeps_safe_facts_without_releasing_queue(self):
        self.start({'authentication_required': True, 'account_issue_code': 'timeout',
                    'account_issue_status': 504, 'retry_after': 30,
                    'request_not_sent': True, 'remote_body': 'secret-fixture',
                    'error': 'authentication Bearer secret-fixture'})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            snapshot = self.fixture.instance.snapshot()
            if snapshot['pause_requested']:
                break
            time.sleep(.01)
        else:
            self.fail('Pause not reached')
        self.assertEqual(snapshot['stage'], 'Aguardando regularização do acesso')
        self.assertIn('inconclusiva', snapshot['current'])
        self.assertNotIn('recusou a autenticação', snapshot['current'])
        self.assertEqual(self.fixture.send.call_count, 1)
        report = json.loads(next(self.fixture.root.glob('campaign_*.json')).read_text('utf8'))
        issue = next(i for i in report['issues'] if i['kind'] == 'authentication_wait')
        facts = next(iter(issue['account_diagnostics'].values()))
        self.assertEqual(facts['retry_after'], 30)
        self.assertEqual(facts['account_issue_status'], 504)
        self.assertNotIn('remote_body', facts)
        self.assertNotIn('secret-fixture', json.dumps(snapshot))
        self.assertTrue(self.fixture.instance.pause_requested)
        self.fixture.mark.assert_not_called()


class TypedDiagnosticFormattingTests(unittest.TestCase):
    def test_prepared_report_to_runner_matrix_without_real_accounts(self):
        for code in ('app_check', 'authentication', 'service', 'timeout', 'version',
                     'policy', 'dns', 'tls'):
            with self.subTest(code=code):
                fixture = fixtures.CampaignEndToEndTests(
                    'test_success_matches_polling_and_persisted_history')
                fixture.setUp()
                try:
                    fixture.send.side_effect = lambda item, account, *a, **kw: {
                        'email': account.email, 'ok': False, 'retryable': False,
                        'account_issue_code': code, 'http_status': 503,
                        'retry_after_seconds': 12, 'request_not_sent': True,
                        'error': 'quality authentication timeout video Bearer secret-fixture'}
                    response = fixture.client.post('/api/campaigns', json=fixture.body)
                    self.assertEqual(response.status_code, 200, response.get_json())
                    snapshot, report = fixture.finish()
                    attempt = report['items'][0]['accounts'][0]
                    self.assertEqual(attempt['account_issue_code'], code)
                    event = next(e for e in snapshot['events'] if e['kind'] == 'account_done')
                    self.assertEqual(event['account_issue_code'], code)
                    self.assertEqual(event['http_status'], 503)
                    self.assertEqual(event['retry_after_seconds'], 12)
                    self.assertTrue(event['request_not_sent'])
                    self.assertIn(runner.friendly_campaign_error(None, attempt), event['detail'])
                    self.assertNotIn('secret-fixture', json.dumps(snapshot))
                    fixture.mark.assert_not_called()
                finally:
                    fixture.instance.stop()
                    if fixture.instance._thread:
                        fixture.instance._thread.join(5)
                    fixture.doCleanups()

    def test_codes_win_over_misleading_auth_video_and_quality_text(self):
        for code, expected in (
            ('access_paused', 'acesso está pausado'), ('app_check', 'App Check'),
            ('authentication', 'autenticação não foi aceita'), ('service', 'instável'),
            ('timeout', 'inconclusiva'), ('version', 'versão'), ('policy', 'política'),
            ('dns', 'DNS'), ('tls', 'TLS'), ('network', 'rede'),
        ):
            with self.subTest(code=code):
                payload = {'account_issue_code': code, 'http_status': 403,
                           'error': 'authentication timeout quality video secret-fixture'}
                text = runner.friendly_campaign_error(payload['error'], payload)
                self.assertIn(expected, text)
                event = runner._public_event('account_done', payload)
                self.assertIn(expected, event['detail'])
                pause = runner._public_event('authentication_wait', {
                    'account_diagnostics': {'fixture@example.invalid': payload}})
                self.assertIn(expected, pause['detail'])
                self.assertNotIn('secret-fixture', json.dumps(event))

    def test_legacy_and_unknown_codes_keep_text_fallback(self):
        for facts in ({}, {'account_issue_code': 'new-private-code'}):
            self.assertIn('demorou', runner.friendly_campaign_error('quality timeout', facts))
        self.assertIn('recusou a autenticação', runner._public_event(
            'authentication_wait', {'accounts': ['fixture@example.invalid']})['detail'])
        self.assertIn('conexão', runner.friendly_campaign_error('DNS name resolution failed'))
        self.assertIn('TLS', runner.friendly_campaign_error('certificate verify failed: secret-fixture'))

    def test_whitelist_rejects_secrets_and_wrong_types(self):
        poisoned = {'account_issue_code': 'Bearer secret-fixture',
                    'blocked_reason': 'secret-fixture', 'http_status': True,
                    'account_issue_status': '403 secret-fixture',
                    'retry_after': '30 secret-fixture', 'retry_after_seconds': float('inf'),
                    'authentication_required': 1, 'request_not_sent': 'true',
                    'appcheck_message': 'secret-fixture', 'headers': {'token': 'secret-fixture'}}
        self.assertEqual(safe_account_diagnostics(poisoned), {})
        self.assertEqual(safe_account_diagnostics({
            'account_issue_code': 'service', 'http_status': 503, 'retry_after_seconds': 12.5,
            'review_required': True}), {
            'account_issue_code': 'service', 'http_status': 503, 'retry_after_seconds': 12.5,
            'review_required': True})
