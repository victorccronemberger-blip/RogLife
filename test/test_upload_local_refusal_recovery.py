"""A proven local refusal preserves diagnosis and resumes the same receipt."""
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from moneymin import campaign, config, minute_api, recovery, registration_proxy, upload
from test_upload_completion_policy import _fixture, _send, _Session


CONTEXT = {'registry_key': 'fixture-key', 'clip_uid': 'fixture-clip', 'task_id': 'fixture-task'}
HEADER = {'X-Firebase-AppCheck': 'fixture-only'}
PERFECT = {'checks': [{'id': 'fixture', 'label': 'Fixture', 'status': 'pass', 'detail': None}]}


def refusal(phase):
    return upload.UploadError('Fixture local refusal', transient=False, phase=phase,
                              blocked_reason='app_check', appcheck_cause='fixture_unavailable',
                              appcheck_message='Fixture diagnostic', request_not_sent=True)


class Session(_Session):
    def ensure_auth(self, **kwargs):
        return {'email': self.email}


class LocalRefusalRecoveryTests(unittest.TestCase):
    @contextmanager
    def fixture(self, count=1):
        with _fixture(count=count) as (root, paths, recorded), \
             patch.object(config, 'DATA_DIR', root), patch.object(config, 'MEDIA_DATA_DIR', root), \
             patch.object(minute_api, 'require_app_check_for_write', return_value=HEADER):
            yield paths, recorded

    def assert_diagnosis(self, value):
        data = value if isinstance(value, dict) else value.to_dict()
        self.assertIs(data['authentication_required'], True)
        self.assertIs(data['request_not_sent'], True)
        self.assertEqual(data['blocked_reason'], 'app_check')
        self.assertEqual(data['appcheck_cause'], 'fixture_unavailable')
        self.assertEqual(data['appcheck_message'], 'Fixture diagnostic')

    def explicit_resume(self):
        restored = Session()
        with patch.object(registration_proxy, 'assign', return_value=None), \
             patch.object(registration_proxy, 'route', return_value=nullcontext()), \
             patch.object(campaign.Session, 'from_email', return_value=restored), \
             patch.object(upload, 'evaluate_upload', return_value=PERFECT), \
             patch.object(recovery, 'reconcile_confirmed', return_value={}):
            recovery.resume_account(restored.email, lambda email: 'fixture-org',
                                    session_id='fixture-session')
        return restored

    def test_local_create_can_explicitly_resume_the_same_sid(self):
        for perfect in (False, True):
            with self.subTest(perfect=perfect), self.fixture() as (paths, recorded):
                first = Session()
                with patch.object(minute_api, 'require_app_check_for_write', side_effect=refusal('create')):
                    result = _send(first, paths, recorded, finalize=True, require_perfect=perfect,
                                   campaign_context=CONTEXT)
                row = upload.load_sidecar('fixture-session', 0)
                self.assert_diagnosis(result.chunks[0])
                self.assert_diagnosis(row)
                self.assertEqual(row['state'], upload.STATE_RETRY_LATE)
                self.assertEqual(row['phase'], 'create')
                self.assertIs(row['create_attempted'], False)
                self.assertEqual(first.calls, [])
                self.assertTrue(recovery.snapshot()['items'][0]['can_resume'])
                restored = self.explicit_resume()
                after = upload.load_sidecar('fixture-session', 0)
                self.assertTrue(after['finalized'])
                self.assertEqual(after['session_id'], row['session_id'])
                self.assertEqual(restored.created, 1)
                self.assertFalse(after['request_not_sent'])
                self.assertIsNone(after['appcheck_cause'])

    def test_second_gate_keeps_monotonic_intent_but_allows_explicit_resume(self):
        class DoubleGateSession(Session):
            def request(self, method, path, body=None):
                if path.startswith('/api/v1/uploads?'):
                    minute_api.require_app_check_for_write(method, path)
                return super().request(method, path, body)

        with self.fixture() as (paths, recorded):
            first = DoubleGateSession()
            with patch.object(minute_api, 'require_app_check_for_write',
                              side_effect=[HEADER, refusal('create')]):
                result = _send(first, paths, recorded, finalize=True, campaign_context=CONTEXT)
            row = upload.load_sidecar('fixture-session', 0)
            self.assert_diagnosis(result.chunks[0])
            self.assertIs(row['create_attempted'], True)
            self.assertFalse(upload._journal_creation_uncertain(row))
            self.assertEqual(first.calls, [])
            self.assertTrue(recovery.snapshot()['items'][0]['can_resume'])
            restored = self.explicit_resume()
            after = upload.load_sidecar('fixture-session', 0)
            self.assertTrue(after['finalized'])
            self.assertIs(after['create_attempted'], True)
            self.assertEqual(restored.created, 1)

    def test_previous_uncertain_create_cannot_acquire_local_proof_on_retry(self):
        class UncertainSession(Session):
            def request(self, method, path, body=None):
                raise TimeoutError('Fixture unknown acknowledgment')

        with self.fixture() as (paths, recorded):
            _send(UncertainSession(), paths, recorded, campaign_context=CONTEXT)
            before = upload.load_sidecar('fixture-session', 0)
            self.assertTrue(upload._journal_creation_uncertain(before))
            self.assertFalse(before['request_not_sent'])
            with patch.object(minute_api, 'require_app_check_for_write', side_effect=refusal('create')) as gate:
                with self.assertRaises(upload.UploadError):
                    _send(Session(), paths, recorded, campaign_context=CONTEXT)
                gate.assert_not_called()
            self.assertEqual(upload.load_sidecar('fixture-session', 0), before)
            self.assertFalse(recovery.snapshot()['items'][0]['can_resume'])

    def test_stages_preserve_diagnosis_and_finalization_clears_it(self):
        for stage in ('sas', 'complete', 'evaluate', 'finalize'):
            with self.subTest(stage=stage), self.fixture() as (paths, recorded):
                class BlockedSession(Session):
                    def request(self, method, path, body=None):
                        if ((stage == 'sas' and path.endswith('/sas/blobs'))
                                or (stage == 'complete' and path.endswith('/complete'))
                                or (stage == 'finalize' and path.endswith('/finalize'))):
                            raise refusal(stage)
                        return super().request(method, path, body)

                with patch.object(upload, 'evaluate_upload',
                                  side_effect=refusal('evaluate') if stage == 'evaluate' else None,
                                  return_value=PERFECT):
                    result = _send(BlockedSession(), paths, recorded, finalize=True, require_perfect=True)
                row = upload.load_sidecar('fixture-session', 0)
                self.assert_diagnosis(result.chunks[0])
                self.assert_diagnosis(row)
                self.assertFalse(result.review_required)
                if stage in ('evaluate', 'finalize', 'complete'):
                    restored = Session()
                    with patch.object(upload, 'evaluate_upload', return_value=PERFECT), \
                         patch.object(upload, '_put_blob_file', side_effect=AssertionError('no media replay')):
                        upload.pump_pending(restored, max_retries=1, session_ids={'fixture-session'})
                    after = upload.load_sidecar('fixture-session', 0)
                    self.assertTrue(after['finalized'])
                    self.assertEqual(after['upload_id'], row['upload_id'])
                    self.assertEqual(restored.created, 0)
                    self.assertFalse(after['request_not_sent'])
                    self.assertIsNone(after['appcheck_message'])

    def test_local_evaluation_without_http_status_resumes_perfect_and_nonperfect_groups(self):
        for perfect, count in ((False, 1), (False, 2), (True, 1), (True, 2)):
            with self.subTest(perfect=perfect, count=count), self.fixture(count) as (paths, recorded):
                failures = [PERFECT] * (count - 1) + [refusal('evaluate')]
                with patch.object(upload, 'evaluate_upload', side_effect=failures):
                    result = _send(Session(), paths, recorded, finalize=True, evaluate=True,
                                   require_perfect=perfect, campaign_context=CONTEXT)
                self.assertFalse(result.review_required)
                self.assertTrue(recovery.snapshot()['items'][0]['can_resume'])
                last = upload.load_sidecar('fixture-session', count - 1)
                self.assertIsNone(last['evaluation_http_status'])
                self.assertTrue(upload.is_pending_evaluation(last))
                restored = self.explicit_resume()
                self.assertEqual(restored.created, 0)
                self.assertTrue(all(upload.load_sidecar('fixture-session', i)['finalized'] for i in range(count)))

    def test_local_complete_keeps_group_receipts_for_stage_specific_recovery(self):
        class BlockedSecondComplete(Session):
            def request(self, method, path, body=None):
                if path.endswith('/fixture-upload-2/complete'):
                    raise refusal('complete')
                return super().request(method, path, body)

        with self.fixture(2) as (paths, recorded):
            with patch.object(upload, 'evaluate_upload', side_effect=AssertionError('local refusal before evaluation')):
                result = _send(BlockedSecondComplete(), paths, recorded, finalize=True,
                               require_perfect=True, campaign_context=CONTEXT)
            self.assertFalse(result.review_required)
            before = [upload.load_sidecar('fixture-session', i) for i in range(2)]
            self.assertEqual(before[0]['phase'], 'done')
            self.assertEqual(before[1]['phase'], 'complete')
            self.assert_diagnosis(before[1])
            self.assertTrue(recovery.snapshot()['items'][0]['can_resume'])
            restored = self.explicit_resume()
            self.assertEqual(restored.created, 0)
            after = [upload.load_sidecar('fixture-session', i) for i in range(2)]
            self.assertTrue(all(row['finalized'] for row in after))
            self.assertEqual([row['upload_id'] for row in before], [row['upload_id'] for row in after])

    def test_remote_authentication_on_known_receipt_preserves_sas_and_complete(self):
        for stage in ('sas', 'complete'):
            for status in (401, 429):
                with self.subTest(stage=stage, status=status), self.fixture() as (paths, recorded):
                    class RemoteRefusal(Session):
                        def request_detailed(self, method, path, body=None):
                            if ((stage == 'sas' and path.endswith('/sas/blobs'))
                                    or (stage == 'complete' and path.endswith('/complete'))):
                                return SimpleNamespace(status=status, text='{}',
                                    headers={'x-blocked-reason': 'app_check'} if status == 429 else {})
                            code, text = self.request(method, path, body)
                            return SimpleNamespace(status=code, text=text, headers={})

                    with patch.object(upload, 'evaluate_upload', side_effect=AssertionError('stage still pending')):
                        result = _send(RemoteRefusal(), paths, recorded, finalize=True,
                                       require_perfect=True, campaign_context=CONTEXT)
                    row = upload.load_sidecar('fixture-session', 0)
                    self.assertFalse(result.review_required)
                    self.assertTrue(result.chunks[0].authentication_required)
                    self.assertFalse(result.chunks[0].request_not_sent)
                    self.assertEqual(row['upload_id'], 'fixture-upload-1')
                    self.assertEqual(row['phase'], stage)
                    self.assertFalse(row['request_not_sent'])
                    self.assertFalse(row['review_required'])
                    self.assertEqual(row['state'], upload.STATE_RETRY_LATE if stage == 'sas'
                                     else upload.STATE_COMPLETING)

    def test_mixed_local_and_remote_authentication_keeps_each_evaluation_status(self):
        for local_first in (False, True):
            with self.subTest(local_first=local_first), self.fixture(2) as (paths, recorded):
                local = refusal('evaluate')
                remote = upload.UploadError('Fixture remote authentication', status_code=401, phase='evaluate')
                errors = [local, remote] if local_first else [remote, local]
                with patch.object(upload, 'evaluate_upload', side_effect=errors):
                    result = _send(Session(), paths, recorded, finalize=True, require_perfect=True,
                                   campaign_context=CONTEXT)
                self.assertFalse(result.review_required)
                rows = [upload.load_sidecar('fixture-session', i) for i in range(2)]
                self.assertEqual([row['evaluation_http_status'] for row in rows],
                                 [error.status_code for error in errors])
                self.assertTrue(all(upload.is_pending_evaluation(row) for row in rows))
                self.assertTrue(recovery.snapshot()['items'][0]['can_resume'])
                self.assertEqual(self.explicit_resume().created, 0)

    def test_local_evaluation_does_not_release_a_sibling_unknown_response(self):
        with self.fixture(2) as (paths, recorded):
            errors = [refusal('evaluate'), upload.UploadError(
                'Fixture unknown response', status_code=401, phase='evaluate', review_required=True)]
            with patch.object(upload, 'evaluate_upload', side_effect=errors):
                result = _send(Session(), paths, recorded, finalize=True, require_perfect=True,
                               campaign_context=CONTEXT)
            self.assertTrue(result.review_required)
            self.assertFalse(recovery.snapshot()['items'][0]['can_resume'])
            self.assertFalse(any(upload.is_pending_evaluation(upload.load_sidecar('fixture-session', i))
                                 for i in range(2)))

    def test_create_local_proof_is_cleared_before_a_new_ambiguous_dispatch(self):
        with self.fixture() as (paths, recorded):
            with patch.object(minute_api, 'require_app_check_for_write', side_effect=refusal('create')):
                _send(Session(), paths, recorded, campaign_context=CONTEXT)
            before = upload.load_sidecar('fixture-session', 0)
            self.assertTrue(before['request_not_sent'])
            observed = []

            class UnknownSession(Session):
                def request(self, method, path, body=None):
                    observed.append(upload.load_sidecar('fixture-session', 0))
                    raise TimeoutError('Fixture unknown acknowledgment')

            _send(UnknownSession(), paths, recorded, campaign_context=CONTEXT)
            self.assertIs(observed[0]['request_not_sent'], False)
            self.assertIsNone(observed[0]['appcheck_cause'])
            after = upload.load_sidecar('fixture-session', 0)
            self.assertTrue(upload._journal_creation_uncertain(after))
            self.assertFalse(after['request_not_sent'])
            self.assertFalse(recovery.snapshot()['items'][0]['can_resume'])

    def test_uncertain_evaluation_is_not_local_or_resumable(self):
        with self.fixture() as (paths, recorded):
            with patch.object(upload, 'evaluate_upload', side_effect=upload.UploadError(
                    'Fixture unknown response', phase='evaluate', blocked_reason='app_check',
                    appcheck_cause='fixture_unavailable', review_required=True)):
                result = _send(Session(), paths, recorded, finalize=True, require_perfect=True,
                               campaign_context=CONTEXT)
            row = upload.load_sidecar('fixture-session', 0)
            self.assertTrue(result.review_required)
            self.assertFalse(row['request_not_sent'])
            self.assertFalse(upload.is_pending_evaluation(row))
            self.assertFalse(recovery.snapshot()['items'][0]['can_resume'])


if __name__ == '__main__':
    unittest.main()
