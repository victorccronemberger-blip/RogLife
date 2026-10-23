"""Typed AuthError -> account delivery -> engine report -> public polling, offline."""
import json
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import test_campaign_end_to_end as fixtures
from moneymin.campaigns import account_delivery
from moneymin.campaigns.execution import safe_account_diagnostics
from moneymin.minute_api import AuthError
from moneymin.upload import UploadError
from moneymin.web import runner


SECRET = 'Bearer secret-fixture password=secret-fixture'


def deliver_failure(error, account=None, item=None):
    """Fail at ensure_auth, before App Check, media, recovery or remote writes."""
    session = Mock()
    session.ensure_auth.side_effect = error
    services = SimpleNamespace(
        AuthError=AuthError, UploadError=UploadError,
        org_policy=SimpleNamespace(account_kind=lambda email: 'other'),
        Session=Mock(), _bind_session_email=Mock(),
        account_registration_route=lambda email: nullcontext(),
        upload_session=Mock())
    services.Session.from_email.return_value = session
    result = account_delivery.upload_to_account(
        services, item or {}, account or SimpleNamespace(
            email='fixture@example.invalid', org_key='fixture-org'),
        'task', 30, True, True)
    session.ensure_auth.assert_called_once()
    services.upload_session.assert_not_called()
    return result


class AccountDeliveryTypedFailureTests(unittest.TestCase):
    def test_codes_reach_public_formatter_without_changing_retry_policy(self):
        for code, text in (
            ('dns', 'DNS'), ('tls', 'TLS'), ('rate_limit', 'prazo indicado'),
            ('service', 'instável'), ('timeout', 'inconclusiva'),
            ('access_paused', 'acesso está pausado'), ('policy', 'política'),
            ('version', 'versão'), ('authentication', 'autenticação não foi aceita'),
            ('app_check', 'App Check'), ('invalid_response', 'validada'),
        ):
            with self.subTest(code=code):
                error = AuthError('quality authentication timeout video ' + SECRET,
                                  code=code, http_status=429,
                                  retry_after_seconds=12.5)
                error.retry_after = 30
                error.request_not_sent = True
                error.remote_body = SECRET
                error.headers = {'Authorization': SECRET}
                error.provider_error_code = SECRET
                result = deliver_failure(error)
                self.assertEqual(result['account_issue_code'], code)
                self.assertEqual(result['http_status'], 429)
                self.assertEqual(result['retry_after'], 30)
                self.assertEqual(result['retry_after_seconds'], 12.5)
                self.assertTrue(result['request_not_sent'])
                self.assertTrue(result['access_error'])
                self.assertFalse(result['ok'])
                self.assertFalse(result['restriction_confirmed'])
                self.assertFalse(result['review_required'])
                self.assertEqual(result['authentication_required'],
                                 code in {'authentication', 'app_check'})
                # DNS remains non-retryable here: adding evidence must not
                # broaden the existing automatic retry policy.
                self.assertEqual(result['retryable'],
                                 code in {'network', 'timeout', 'service', 'rate_limit'})
                for field in ('remote_body', 'headers', 'provider_error_code'):
                    self.assertNotIn(field, result)
                facts = safe_account_diagnostics(result)
                public = runner._public_event('account_done', result)
                self.assertIn(text, public['detail'])
                self.assertNotIn('secret-fixture', json.dumps(public))
                self.assertNotIn('secret-fixture', json.dumps(facts))

    def test_deadlines_follow_safe_contract_for_both_aliases(self):
        values = (None, True, False, '12', SECRET, {}, [], -1, -.1,
                  float('nan'), float('inf'), float('-inf'), 86401, 10 ** 400,
                  0, 0.0, .25, 12, 86400)
        for field in ('retry_after', 'retry_after_seconds'):
            for value in values:
                with self.subTest(field=field, value=value):
                    error = AuthError('fixture', code='rate_limit')
                    setattr(error, field, value)
                    result = deliver_failure(error)
                    accepted = type(value) in (int, float) and 0 <= value <= 86400
                    if accepted:
                        self.assertEqual(result[field], value)
                        self.assertEqual(safe_account_diagnostics(result)[field], value)
                    else:
                        self.assertNotIn(field, result)
                    # Strict JSON cannot represent NaN/Inf; no such value escapes.
                    json.dumps(result, allow_nan=False)

    def test_unknown_or_malformed_codes_and_statuses_do_not_copy_remote_data(self):
        for code in (None, 'unknown', 'new-provider-failure ' + SECRET, [], {}):
            for status in (None, True, '429', -2, 0, 600, float('nan')):
                with self.subTest(code=code, status=status):
                    error = AuthError(SECRET, code=code, http_status=status)
                    result = deliver_failure(error)
                    self.assertNotIn('http_status', result)
                    if code == 'unknown':
                        self.assertEqual(result['account_issue_code'], 'unknown')
                    else:
                        self.assertNotIn('account_issue_code', result)
                    self.assertFalse(result['authentication_required'])
                    self.assertFalse(result['retryable'])
                    self.assertNotIn('secret-fixture', json.dumps(
                        runner._public_event('account_done', result)))

    def test_review_and_restriction_flags_remain_unchanged(self):
        error = AuthError('fixture', code='service', http_status=503,
                          retry_after_seconds=15)
        error.remote_effect_possible = True
        result = deliver_failure(error)
        self.assertTrue(result['review_required'])
        self.assertFalse(result['retryable'])
        self.assertEqual(result['retry_after_seconds'], 15)
        result = deliver_failure(AuthError('fixture', code='restricted', http_status=403))
        self.assertTrue(result['restriction_confirmed'])
        self.assertFalse(result['retryable'])


class AccountDeliveryTypedFailureEndToEndTests(unittest.TestCase):
    def test_real_delivery_engine_report_and_public_polling(self):
        for code, text, delay in (
            ('dns', 'DNS', 12.5), ('rate_limit', 'prazo indicado', 12.5),
            ('unknown', 'concluído', 12.5), ('dns', 'DNS', float('nan')),
            ('dns', 'DNS', float('inf')), ('dns', 'DNS', -1),
        ):
            with self.subTest(code=code, delay=delay):
                fixture = fixtures.CampaignEndToEndTests(
                    'test_success_matches_polling_and_persisted_history')
                fixture.setUp()
                try:
                    def send(item, account, *args, **kwargs):
                        error = AuthError(SECRET, code=code, http_status=429,
                                          retry_after_seconds=delay)
                        error.retry_after = delay
                        error.remote_body = SECRET
                        return deliver_failure(error, account, item)
                    fixture.send.side_effect = send
                    response = fixture.client.post('/api/campaigns', json=fixture.body)
                    self.assertEqual(response.status_code, 200, response.get_json())
                    snapshot, report = fixture.finish()
                    attempts = report['items'][0]['accounts']
                    self.assertTrue(attempts)
                    events = [event for event in snapshot['events']
                              if event['kind'] == 'account_done']
                    self.assertTrue(events, snapshot)
                    for row in [*attempts, *events]:
                        self.assertEqual(row['account_issue_code'], code)
                        self.assertEqual(row['http_status'], 429)
                        for field in ('retry_after', 'retry_after_seconds'):
                            if delay == 12.5:
                                self.assertEqual(row[field], delay)
                            else:
                                self.assertNotIn(field, row)
                        self.assertNotIn('remote_body', row)
                    for event in events:
                        self.assertIn(text, event['detail'])
                    self.assertNotIn('secret-fixture', json.dumps(snapshot))
                    fixture.mark.assert_not_called()
                finally:
                    fixture.instance.stop()
                    if fixture.instance._thread:
                        fixture.instance._thread.join(5)
                    fixture.doCleanups()
