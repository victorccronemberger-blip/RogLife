"""Offline campaign admission against the real quota gate and inert sessions."""
from contextlib import contextmanager
import json
import threading
import unittest
from unittest.mock import Mock, patch

from moneymin import campaign, minute_api
from moneymin.campaign_types import AccountSpec, CampaignConfig, TaskSpec
from moneymin.web import server
import test_campaign_receipt_admission as receipts


APPROVED = {"recordingAuthorization": "APPROVED", "canUpload": True, "blockedReason": None}
VERSION_BODY_MARKER = 'private-fixture-quota-response-body'
VERSION_DENIAL = {"detail": {"error": "app_version_too_old", "min_version": "1.30.0",
                             "message": VERSION_BODY_MARKER}}


class CampaignRecordingAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = receipts.CampaignReceiptAdmissionTests(
            'test_valid_receipt_keeps_catalog_and_exact_operation')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        f = self.fixture
        f.stack.enter_context(patch.object(server.config, 'APP_VERSION', '1.29.0'))
        self.responses = {f.email: (200, APPROVED)}
        self.quality_response = minute_api.HttpResponse(200, json.dumps({'userState': 'active'}), {})
        self.queries = []
        self.reads = []
        self.sessions = []
        self.quota_barrier = None
        self.route = threading.local()

        @contextmanager
        def identity_route(email):
            previous = getattr(self.route, 'email', None)
            self.route.email = email
            try:
                yield
            finally:
                self.route.email = previous

        def from_email(email):
            self.assertEqual(self.route.email, email)
            session = minute_api.Session({'email': email, 'idToken': 'inert-token',
                                          'refreshToken': 'inert-refresh'})
            session.ensure_auth = Mock(return_value={})
            session.all_tasks = Mock(return_value=[{'id': 'fixture-task', 'name': 'Fixture'}])

            def quota(method, path, body=None):
                self.assertEqual(self.route.email, email)
                self.assertEqual(method, 'GET')
                self.assertIsNone(body)
                self.reads.append((email, path))
                if path == '/api/v1/users/me':
                    return minute_api.HttpResponse(200, json.dumps({
                        'disabled': False, 'organizations': [
                            {'resourceKey': 'fixture-org', 'disabled': False}]}), {})
                if path == '/api/v1/organizations/fixture-org/quality-summary':
                    return self.quality_response
                self.assertEqual(path, '/api/v1/orgs/fixture-org/quota')
                self.assertIsNone(body)
                self.queries.append((email, path))
                if self.quota_barrier is not None:
                    self.quota_barrier.wait(timeout=3)
                status, payload = self.responses[email]
                text = json.dumps(payload)
                if status == 403:
                    # Session.request_detailed normally records the observed version.
                    # This fixture stops at the read boundary, without transport.
                    minute_api._maybe_latch_version_gate(text)
                return minute_api.HttpResponse(status, text, {})

            session.request_detailed = Mock(side_effect=quota)
            def request(method, path, body=None):
                response = quota(method, path, body)
                return response.status, response.text

            session.request = Mock(side_effect=request)
            self.sessions.append(session)
            return session

        def resolve(email, *, session):
            self.assertEqual(self.route.email, email)
            self.assertEqual(session.email, email)
            self.assertIn(session, self.sessions)
            return 'fixture-org'

        f.stack.enter_context(patch.object(server, '_identity_route', identity_route))
        f.stack.enter_context(patch.object(server, '_resolve_org', side_effect=resolve))
        f.session.side_effect = from_email
        f.catalog.reset_mock()
        self.forbidden = {}
        for name in ('automatic_candidates', '_ego_clip_inputs', 'prepare_clip',
                     'prepare_holoassist_clip', 'prepare_nymeria_clip',
                     '_prefetch_following', 'upload_to_account', 'upload_session'):
            self.forbidden[name] = f.stack.enter_context(patch.object(
                campaign, name, side_effect=AssertionError('media/network forbidden in admission')))
        self.network = f.stack.enter_context(patch.object(
            minute_api, '_request', side_effect=AssertionError('no real Minute request')))
        self.network_detailed = f.stack.enter_context(patch.object(
            minute_api, '_request_detailed', side_effect=AssertionError('no real detailed Minute request')))

    def assert_no_effects(self):
        self.fixture.runner.start.assert_not_called()
        self.fixture.ban.assert_not_called()
        self.network.assert_not_called()
        self.network_detailed.assert_not_called()
        for operation in self.forbidden.values():
            operation.assert_not_called()
        self.assertTrue(self.fixture.token.exists())
        self.assertIsNone(server.campaign_start_store.lookup(self.fixture.receipt))

    def test_quota_403_version_for_two_accounts_never_grants_receipt_or_prepares(self):
        f = self.fixture
        other = 'second-owner@example.invalid'
        server.config.token_path(other).write_text(json.dumps({
            'email': other, 'idToken': 'inert-second', 'refreshToken': 'inert-refresh'}))
        self.responses = {email: (403, VERSION_DENIAL) for email in (f.email, other)}
        self.quota_barrier = threading.Barrier(2)
        with patch.object(server, '_list_accounts', return_value=[
                {'email': email} for email in self.responses]):
            response = f.client.post('/api/campaigns/preflight', json={
                **f.body, 'accounts': list(self.responses), 'include_clip_plan': True})
        self.assertEqual(response.status_code, 409, response.get_json())
        data = response.get_json()
        self.assertFalse(data['ok'])
        self.assertIsNone(data['preflight_id'])
        self.assertEqual(data['error_code'], 'version')
        self.assertIn('1.29.0', data['error'])
        self.assertIn('1.30.0', data['error'])
        self.assertNotIn(VERSION_BODY_MARKER, response.get_data(as_text=True))
        self.assertEqual(len(self.queries), 2)
        self.assertEqual(len(data['account_issues']), 2)
        for issue in data['account_issues']:
            self.assertEqual(issue['code'], 'version')
            self.assertEqual(issue['http_status'], 403)
            self.assertFalse(issue['restriction_confirmed'])
        self.assertFalse(data['can_remove_and_continue'])
        f.catalog.assert_not_called()
        self.assert_no_effects()

    def test_known_version_blocks_preflight_legacy_and_reviewed_start_before_session(self):
        f = self.fixture
        minute_api._maybe_latch_version_gate(json.dumps(VERSION_DENIAL))
        state = minute_api._version_gate_state()
        for path, body in (('/api/campaigns/preflight', f.body),
                           ('/api/campaigns', f.body),
                           ('/api/campaigns', f.start_body)):
            with self.subTest(path=path, reviewed='preflight_id' in body):
                response = f.client.post(path, json=body)
                self.assertEqual(response.status_code, 409, response.get_json())
                self.assertEqual(response.json['error_code'], 'version')
                self.assertIn('1.29.0', response.json['error'])
                self.assertIn('1.30.0', response.json['error'])
                self.assertNotIn(VERSION_BODY_MARKER, response.get_data(as_text=True))
        self.assertEqual(minute_api._version_gate_state(), state)
        f.session.assert_not_called()
        f.catalog.assert_not_called()
        self.assert_no_effects()

    def test_quota_version_after_review_blocks_start_and_preserves_receipt(self):
        f = self.fixture
        self.responses[f.email] = (403, VERSION_DENIAL)
        response = f.client.post('/api/campaigns', json=f.start_body)
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(response.json['error_code'], 'version')
        self.assertEqual(response.json['account_issues'][0]['http_status'], 403)
        self.assertIn('1.30.0', response.json['error'])
        self.assertNotIn(VERSION_BODY_MARKER, response.get_data(as_text=True))
        self.assertEqual(len(self.queries), 1)
        f.catalog.assert_not_called()
        self.assert_no_effects()

    def test_unknown_or_denied_quota_after_review_cannot_fail_open(self):
        f = self.fixture
        for payload, code in (({}, 'invalid_response'),
                              ({'recordingAuthorization': 'BLOCKED', 'canUpload': False,
                                'blockedReason': 'quota_exceeded'}, 'policy'),
                              ({**APPROVED, 'canUpload': 'true'}, 'invalid_response')):
            with self.subTest(payload=payload):
                self.responses[f.email] = (200, payload)
                response = f.client.post('/api/campaigns', json=f.start_body)
                self.assertEqual(response.status_code, 400, response.get_json())
                self.assertEqual(response.json['error_code'], code)
                self.assertFalse(response.json['account_issues'][0]['restriction_confirmed'])
                self.assert_no_effects()
        self.assertEqual(len(self.queries), 3, 'start must query a fresh session every time')
        self.responses[f.email] = (200, APPROVED)
        response = f.client.post('/api/campaigns', json=f.start_body)
        self.assertEqual(response.status_code, 200, response.get_json())
        f.runner.start.assert_called_once()
        self.assertEqual(len(self.queries), 4)
        f.catalog.assert_not_called()
        f.ban.assert_not_called()

    def test_preflight_unknown_or_denied_quota_never_grants_receipt_or_bans(self):
        f = self.fixture
        for status, payload, code in ((200, {}, 'invalid_response'),
                                      (503, {'detail': 'inert-service-error'}, 'service'),
                                      (200, {'recordingAuthorization': 'BLOCKED',
                                             'canUpload': False, 'blockedReason': 'manual'}, 'policy')):
            with self.subTest(status=status, payload=payload):
                self.responses[f.email] = (status, payload)
                response = f.client.post('/api/campaigns/preflight', json=f.body)
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertFalse(response.json['ok'])
                self.assertIsNone(response.json['preflight_id'])
                self.assertEqual(response.json['account_issues'][0]['code'], code)
                self.assertEqual(response.json['removed_accounts'], [])
                self.assert_no_effects()
        f.catalog.assert_not_called()

    def test_approved_quota_is_checked_before_receipt_and_again_at_start(self):
        f = self.fixture
        review = f.client.post('/api/campaigns/preflight', json=f.body)
        self.assertEqual(review.status_code, 200, review.get_json())
        self.assertTrue(review.json['ok'])
        self.assertTrue(review.json['preflight_id'])
        self.assertEqual(len(self.queries), 1)
        first_session = self.sessions[0]
        response = f.client.post('/api/campaigns', json={
            **f.body, 'preflight_id': review.json['preflight_id']})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(len(self.queries), 2)
        self.assertIsNot(first_session, self.sessions[-1])
        self.assertEqual([path for _, path in self.reads], [
            '/api/v1/users/me', '/api/v1/organizations/fixture-org/quality-summary',
            '/api/v1/orgs/fixture-org/quota'] * 2)
        f.runner.start.assert_called_once()
        self.network.assert_not_called()
        self.network_detailed.assert_not_called()
        f.ban.assert_not_called()
        for operation in self.forbidden.values():
            operation.assert_not_called()

    def test_unverified_quality_blocks_review_and_both_start_contracts_before_quota(self):
        f = self.fixture
        for status, payload, code in (
                (200, {'userState': 'unknown'}, 'invalid_response'),
                (200, {}, 'invalid_response'),
                (200, {'userState': 'on_hold'}, 'access_paused'),
                (200, {'userState': 'inactive'}, 'access_paused'),
                (503, {'detail': 'unavailable'}, 'service')):
            self.quality_response = minute_api.HttpResponse(status, json.dumps(payload), {})
            for path, body in (('/api/campaigns/preflight', f.body),
                               ('/api/campaigns', f.body),
                               ('/api/campaigns', f.start_body)):
                with self.subTest(status=status, payload=payload, path=path,
                                  reviewed='preflight_id' in body):
                    response = f.client.post(path, json=body)
                    self.assertEqual(response.status_code, 200 if path.endswith('preflight') else 400,
                                     response.get_json())
                    self.assertEqual(response.json['account_issues'][0]['code'], code)
                    if path.endswith('preflight'):
                        self.assertFalse(response.json['ok'])
                        self.assertIsNone(response.json['preflight_id'])
                    self.assertEqual(self.queries, [], 'quality must be verified before quota')
                    f.catalog.assert_not_called()
                    self.assert_no_effects()

    def test_legacy_start_preserves_quota_category_and_status_without_banning(self):
        f = self.fixture
        for status, payload, code in ((503, {'detail': 'inert-service-error'}, 'service'),
                                      (403, {'detail': 'Forbidden'}, 'forbidden'),
                                      (200, {}, 'invalid_response')):
            with self.subTest(status=status, code=code):
                self.responses[f.email] = (status, payload)
                response = f.client.post('/api/campaigns', json=f.body)
                self.assertEqual(response.status_code, 400, response.get_json())
                self.assertEqual(response.json['error_code'], code)
                issue = response.json['account_issues'][0]
                self.assertEqual(issue['code'], code)
                if status != 200:
                    self.assertEqual(issue['http_status'], status)
                self.assertFalse(issue['restriction_confirmed'])
                self.assert_no_effects()
        f.catalog.assert_not_called()

    def test_every_reviewed_account_must_still_be_authorized_at_start(self):
        f = self.fixture
        other = 'second-owner@example.invalid'
        server.config.token_path(other).write_text(json.dumps({
            'email': other, 'idToken': 'inert-second', 'refreshToken': 'inert-refresh'}))
        self.responses[other] = (200, APPROVED)
        body = {**f.body, 'accounts': [f.email, other]}
        with patch.object(server, '_list_accounts', return_value=[{'email': email} for email in body['accounts']]):
            review = f.client.post('/api/campaigns/preflight', json=body)
            self.assertEqual(review.status_code, 200, review.get_json())
            self.assertTrue(review.json['ok'], review.get_json())
            self.assertEqual(review.json['accounts']['validated'], 2)
            self.assertEqual({email for email, _ in self.queries}, set(body['accounts']))
            self.responses[other] = (200, {'recordingAuthorization': 'BLOCKED',
                                          'canUpload': False, 'blockedReason': 'quota_exceeded'})
            response = f.client.post('/api/campaigns', json={**body, 'preflight_id': review.json['preflight_id']})
        self.assertEqual(response.status_code, 400, response.get_json())
        self.assertEqual(response.json['account_issues'][0]['email'], other)
        self.assertEqual(response.json['error_code'], 'policy')
        self.assertEqual(len(self.queries), 4)
        self.assertIsNone(server.campaign_start_store.lookup(review.json['preflight_id']))
        self.assert_no_effects()

    def test_direct_execution_known_version_never_acquires_prepares_or_prefetches(self):
        f = self.fixture
        minute_api._maybe_latch_version_gate(json.dumps(VERSION_DENIAL))
        cfg = CampaignConfig([AccountSpec(f.email, 'fixture-org')],
                             [TaskSpec('fixture-task', 'fixture', 60, 300)],
                             work_dir=f.root / 'work', cleanup_after_upload=False)
        prefetch = Mock()
        with patch.object(campaign, '_ClipPrefetch', return_value=prefetch):
            with self.assertRaises(minute_api.AuthError) as raised:
                campaign.run_campaign(cfg)
        self.assertEqual(raised.exception.account_issue_code, 'version')
        self.assertIn('1.30.0', str(raised.exception))
        prefetch.submit.assert_not_called()
        prefetch.take.assert_not_called()
        self.assertFalse(cfg.work_dir.exists())
        self.assert_no_effects()
