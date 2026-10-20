"""An unknown CREATE acknowledgment must not become a second creation on restart."""
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from moneymin import appcheck, config, recovery, upload
import test_upload_response_contract as response_fixtures


class UploadCreateAmbiguityTests(unittest.TestCase):
    def setUp(self):
        fixture = response_fixtures.UploadResponseContractTests('test_new_upload_defaults_register_before_sas_and_transport')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.root = fixture.video.parent
        self.journals = self.root / 'journals'
        self.journals.mkdir()
        fixture.stack.enter_context(patch.object(config, 'DATA_DIR', self.root))
        fixture.stack.enter_context(patch.object(config, 'MEDIA_DATA_DIR', self.root))
        fixture.stack.enter_context(patch.object(upload, 'sidecars_dir', return_value=self.journals))
        self.clock = upload.format_recorded_at(time.time() - 300)

    def session(self):
        session = response_fixtures.FakeSession()
        session.email = 'create@example.invalid'
        return session

    def row(self, sid='ambiguous', **changes):
        return {'session_id': sid, 'chunk_index': 0, 'expected_chunk_count': 1,
                'account_email': 'create@example.invalid', 'org_key': 'fixture-org',
                'task_id': 'fixture-task', 'state': 'creating', 'phase': 'preflight',
                'recorded_at': self.clock, 'local_video_path': str(self.fixture.video),
                'log_id': sid + '_0', 'filename': sid + '_0.mp4',
                'finalize_requested': False, 'evaluation_required': False,
                'campaign_context': {'registry_key': 'fixture-key', 'clip_uid': 'fixture-clip', 'task_id': 'fixture-task'},
                **changes}

    def send(self, session, sid='ambiguous'):
        return upload.upload_session(session, self.fixture.video, 'fixture-org',
            task_id='fixture-task', session_id=sid, recorded_at=self.clock,
            normalize=False, finalize=False, evaluate=False, persist_sidecar=True,
            device_meta={}, platform_meta={}, video_meta={}, network_meta={},
            max_retries=1, retry_backoff=0, fail_on_error=False)

    def test_creation_intent_is_committed_before_provider_request(self):
        session = self.session()
        real_request = session.request
        observed = []
        def request(method, path, body=None):
            if path.startswith('/api/v1/uploads?'):
                observed.append(upload.load_sidecar('new-intent', 0))
            return real_request(method, path, body)
        session.request = request
        result = self.send(session, 'new-intent')
        self.assertEqual(len(observed), 1)
        self.assertIs(observed[0].get('create_attempted'), True)
        self.assertEqual(observed[0]['phase'], 'create_in_flight')
        self.assertEqual(result.chunks[0].upload_id, 'fixture-upload')

    def test_local_app_check_failure_does_not_mark_create_attempted(self):
        session = self.session()
        with patch.object(appcheck, 'get_app_check_header', return_value={}), \
             patch.object(appcheck, 'app_check_status', return_value={
                 'token_available': False, 'source': None, 'device': {},
                 'failure': 'device_unavailable',
                 'diagnostic': {
                     'category': 'device_unavailable',
                     'message': 'Dispositivo sem token Play Integrity',
                 },
             }):
            result = self.send(session, 'local-appcheck')
        row = upload.load_sidecar('local-appcheck', 0)
        self.assertIsNot(row.get('create_attempted'), True)
        self.assertEqual(session.calls, [])
        chunk = result.chunks[0]
        self.assertTrue(chunk.authentication_required)
        self.assertEqual(chunk.blocked_reason, 'app_check')
        self.assertEqual(chunk.appcheck_cause, 'device_unavailable')
        self.assertIn('Play Integrity', chunk.appcheck_message or '')
        self.assertFalse(chunk.review_required)

    def test_local_auth_error_before_dispatch_is_not_uncertain_create(self):
        from moneymin import minute_api

        session = self.session()
        original = session.request
        context = {'registry_key': 'fixture-key', 'clip_uid': 'fixture-clip',
                   'task_id': 'fixture-task'}

        def request(method, path, body=None):
            if path.startswith('/api/v1/uploads?'):
                raise minute_api.AuthError(
                    'Fixture: before dispatch', code='authentication', http_status=401)
            return original(method, path, body)

        session.request = request
        result = upload.upload_session(
            session, self.fixture.video, 'fixture-org',
            task_id='fixture-task', session_id='local-auth', recorded_at=self.clock,
            normalize=False, finalize=False, evaluate=False, persist_sidecar=True,
            device_meta={}, platform_meta={}, video_meta={}, network_meta={},
            max_retries=1, retry_backoff=0, fail_on_error=False,
            campaign_context=context)
        row = upload.load_sidecar('local-auth', 0)
        chunk = result.chunks[0]
        self.assertTrue(chunk.request_not_sent)
        self.assertTrue(chunk.authentication_required)
        self.assertFalse(chunk.review_required)
        self.assertEqual(row['phase'], 'create')
        self.assertTrue(row['request_not_sent'])
        self.assertTrue(row['authentication_required'])
        self.assertFalse(str(row.get('upload_id') or '').strip())
        self.assertFalse(upload._journal_creation_uncertain(row))
        item = next(item for item in recovery.snapshot()['items']
                    if item['session_id'] == 'local-auth')
        self.assertTrue(item['can_resume'])
        self.assertFalse(item['blocks_campaign'])

    def test_auth_error_with_remote_effect_keeps_uncertain_create(self):
        from moneymin import minute_api

        session = self.session()
        original = session.request

        def request(method, path, body=None):
            if path.startswith('/api/v1/uploads?'):
                error = minute_api.AuthError(
                    'Fixture: after possible dispatch', code='authentication', http_status=401)
                error.remote_effect_possible = True
                raise error
            return original(method, path, body)

        session.request = request
        with self.assertRaises(minute_api.AuthError):
            self.send(session, 'remote-auth')
        row = upload.load_sidecar('remote-auth', 0)
        self.assertEqual(row['phase'], 'create_in_flight')
        self.assertTrue(row['create_attempted'])
        self.assertFalse(row.get('request_not_sent', False))
        self.assertTrue(upload._journal_creation_uncertain(row))
        item = next(item for item in recovery.snapshot()['items']
                    if item['session_id'] == 'remote-auth')
        self.assertEqual(item['status'], 'needs_review')
        self.assertIs(item['can_resume'], False)

    def test_unknown_creation_blocks_direct_send_and_pending_driver_without_rewrite(self):
        for mode in ('direct', 'pump'):
            sid = 'unknown-' + mode
            row = self.row(sid, create_attempted=True, phase='create_in_flight')
            path = upload.save_sidecar(row)
            before = path.read_bytes()
            session = self.session()
            with self.subTest(mode=mode):
                item = next(item for item in recovery.snapshot()['items'] if item['session_id'] == sid)
                self.assertEqual(item['status'], 'needs_review')
                self.assertIs(item['can_resume'], False)
                with self.assertRaises(upload.UploadError):
                    if mode == 'direct': self.send(session, sid)
                    else: upload.pump_pending(session, account_email=session.email, session_ids={sid})
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(session.calls, [])

    def test_legacy_unknown_phases_are_reviewed_and_explicit_no_attempt_is_supported(self):
        for phase in ('preflight', 'sidecar_validated', 'create', 'create_in_flight', 'registered', ''):
            with self.subTest(legacy_phase=phase), self.assertRaises(upload.UploadError):
                upload._pending_recovery_stage(self.row(phase=phase))
        self.assertEqual(upload._pending_recovery_stage(self.row(phase='queued')), 'upload')
        self.assertEqual(upload._pending_recovery_stage(self.row(create_attempted=False)), 'upload')
        with self.assertRaises(upload.UploadError):
            upload._pending_recovery_stage(self.row(create_attempted=False, phase='create_in_flight'))
        session = self.session()
        upload.save_sidecar(self.row('never-attempted', create_attempted=False))
        result = self.send(session, 'never-attempted')
        self.assertEqual(result.chunks[0].upload_id, 'fixture-upload')

    def test_malformed_creation_flag_is_rejected_before_effects(self):
        for index, value in enumerate((None, 0, 1, 'false', [], {})):
            sid = 'invalid-flag-' + str(index)
            path = upload.save_sidecar(self.row(sid, create_attempted=value))
            before = path.read_bytes()
            session = self.session()
            with self.subTest(value=value), self.assertRaises(upload.UploadError):
                self.send(session, sid)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(session.calls, [])

    def test_known_receipt_still_uses_only_supported_completion_stage(self):
        for phase in ('transport_done', 'completing', 'complete'):
            with self.subTest(phase=phase):
                self.assertEqual(upload._pending_recovery_stage(self.row(
                    upload_id='accepted-original', create_attempted=True, phase=phase)), 'complete')
