"""Authentication refusal preserves receipts without remote compensation."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from moneymin import appcheck, campaign, config, upload
from moneymin.campaign_types import AccountSpec
from test_upload_completion_policy import _fixture, _send, _Session


class UploadAuthenticationSuspensionTests(unittest.TestCase):
    def setUp(self):
        header = patch.object(appcheck, 'get_app_check_header',
                              return_value={'X-Firebase-AppCheck': 'fixture-appcheck'})
        header.start()
        self.addCleanup(header.stop)

    def test_refusal_stops_more_chunks_and_never_marks_remote_upload_failed(self):
        for stage in ('create', 'sas', 'complete'):
            with self.subTest(stage=stage), _fixture(count=2) as (_, paths, recorded):
                session = _Session()
                calls = []

                def detailed(method, path, body=None):
                    calls.append((method, path))
                    denied = ((stage == 'create' and path.startswith('/api/v1/uploads?'))
                              or (stage == 'sas' and path.endswith('/sas/blobs'))
                              or (stage == 'complete' and path.endswith('/complete')))
                    if denied:
                        return SimpleNamespace(status=429, text='{}',
                                               headers={'x-blocked-reason': 'app_check'})
                    status, text = session.request(method, path, body)
                    return SimpleNamespace(status=status, text=text, headers={})

                session.request_detailed = detailed
                with patch.object(upload, 'fail_upload', side_effect=AssertionError('no compensation')) as fail:
                    result = _send(session, paths, recorded, register_first=True, fail_on_error=True)
                self.assertFalse(result.finalized)
                self.assertEqual(len(result.chunks), 1)
                self.assertTrue(result.chunks[0].authentication_required)
                self.assertTrue(result.chunks[0].to_dict()['authentication_required'])
                self.assertEqual(sum(path.startswith('/api/v1/uploads?') for _, path in calls), 1)
                fail.assert_not_called()
                self.assertTrue(all(path.exists() for path in paths))

    def test_finalize_refusal_reaches_session_result(self):
        with _fixture() as (_, paths, recorded):
            session = _Session()
            session.finalize_status = 401
            result = _send(session, paths, recorded, finalize=True)
            self.assertFalse(result.finalized)
            self.assertTrue(result.authentication_required)
            self.assertTrue(result.to_dict()['authentication_required'])
            self.assertEqual(session.created, 1)

    def test_restart_recovers_only_finalize_after_external_authentication_correction(self):
        with _fixture() as (_, paths, recorded):
            expired = _Session()
            expired.finalize_status = 401
            _send(expired, paths, recorded, finalize=True)
            before = upload.load_sidecar('fixture-session', 0)
            self.assertTrue(before['authentication_required'])
            self.assertEqual(before['state'], upload.STATE_COMPLETING)
            restored = _Session()  # New process/session reading the existing journal.
            with patch.object(upload, '_put_blob_file', side_effect=AssertionError('no video replay')), \
                 patch.object(upload, '_put_blob', side_effect=AssertionError('no sidecar replay')):
                upload.pump_pending(restored, max_retries=1,
                                    required_org_key='fixture-org', session_ids={'fixture-session'})
            after = upload.load_sidecar('fixture-session', 0)
            self.assertTrue(after['finalized'])
            self.assertFalse(after['authentication_required'])
            self.assertEqual(after['upload_id'], before['upload_id'])
            self.assertEqual(after['session_id'], before['session_id'])
            self.assertEqual(restored.created, 0)
            self.assertEqual(len(restored.calls), 1)
            self.assertTrue(restored.calls[0][1].endswith('/finalize'))

    def test_failed_revalidation_keeps_finalize_pending_without_creating_upload(self):
        with _fixture() as (_, paths, recorded):
            expired = _Session()
            expired.finalize_status = 401
            _send(expired, paths, recorded, finalize=True)
            restored = _Session()
            restored.finalize_status = 401
            upload.pump_pending(restored, max_retries=1,
                                required_org_key='fixture-org', session_ids={'fixture-session'})
            after = upload.load_sidecar('fixture-session', 0)
            self.assertFalse(after['finalized'])
            self.assertTrue(after['authentication_required'])
            self.assertEqual(after['state'], upload.STATE_COMPLETING)
            self.assertEqual(restored.created, 0)

    def test_restart_resumes_complete_and_finalize_on_the_original_receipt(self):
        with _fixture() as (_, paths, recorded):
            expired = _Session()

            def detailed(method, path, body=None):
                if path.endswith('/complete'):
                    return SimpleNamespace(status=401, text='{}', headers={})
                status, text = expired.request(method, path, body)
                return SimpleNamespace(status=status, text=text, headers={})

            expired.request_detailed = detailed
            result = _send(expired, paths, recorded, finalize=True)
            self.assertTrue(result.chunks[0].authentication_required)
            original_id = result.chunks[0].upload_id
            restored = _Session()
            with patch.object(upload, '_put_blob_file', side_effect=AssertionError('no video replay')), \
                 patch.object(upload, '_put_blob', side_effect=AssertionError('no sidecar replay')):
                upload.pump_pending(restored, max_retries=1,
                                    required_org_key='fixture-org', session_ids={'fixture-session'})
            after = upload.load_sidecar('fixture-session', 0)
            self.assertTrue(after['finalized'])
            self.assertFalse(after['authentication_required'])
            self.assertEqual(after['upload_id'], original_id)
            self.assertEqual(restored.created, 0)
            self.assertEqual([path.rsplit('/', 1)[-1] for _, path, _ in restored.calls],
                             ['complete', 'finalize'])

    def test_evaluation_resumes_after_external_correction_without_transport(self):
        with _fixture() as (_, paths, recorded):
            expired = _Session()
            with patch.object(upload, 'evaluate_upload', side_effect=upload.UploadError(
                    'Evaluation authentication refused', status_code=401, phase='evaluate')):
                result = _send(expired, paths, recorded, evaluate=True, finalize=True)
            self.assertTrue(result.chunks[0].authentication_required)
            before = upload.load_sidecar('fixture-session', 0)
            self.assertTrue(upload.is_pending_evaluation(before))
            restored = _Session()
            perfect = {'upload_id': before['upload_id'], 'checks': [
                {'id': 'fixture', 'label': 'Fixture', 'status': 'pass', 'detail': None}]}
            with patch.object(upload, 'evaluate_upload', return_value=perfect) as evaluate, \
                 patch.object(upload, '_put_blob_file', side_effect=AssertionError('no video replay')):
                upload.pump_pending(restored, max_retries=1,
                                    required_org_key='fixture-org', session_ids={'fixture-session'})
            evaluate.assert_called_once_with(restored, before['upload_id'])
            after = upload.load_sidecar('fixture-session', 0)
            self.assertTrue(after['finalized'])
            self.assertFalse(after['authentication_required'])
            self.assertEqual(after['upload_id'], before['upload_id'])
            self.assertEqual(restored.created, 0)

    def test_unclassified_forbidden_evaluation_does_not_become_authentication_retry(self):
        with _fixture() as (_, paths, recorded):
            with patch.object(upload, 'evaluate_upload', side_effect=upload.UploadError(
                    'Forbidden', status_code=403, phase='evaluate')):
                _send(_Session(), paths, recorded, evaluate=True, finalize=True)
            row = upload.load_sidecar('fixture-session', 0)
            self.assertFalse(row['authentication_required'])
            self.assertFalse(upload.is_pending_evaluation(row))

    def test_perfect_group_rechecks_quality_after_authentication_without_new_uploads(self):
        with _fixture(count=2) as (_, paths, recorded):
            perfect = {'checks': [{'id': 'fixture', 'label': 'Fixture', 'status': 'pass', 'detail': None}]}
            with patch.object(upload, 'evaluate_upload', side_effect=[perfect, upload.UploadError(
                    'Expired access', status_code=401, phase='evaluate')]):
                result = _send(_Session(), paths, recorded, require_perfect=True, finalize=True)
            self.assertFalse(result.finalized)
            before = [upload.load_sidecar('fixture-session', i) for i in range(2)]
            self.assertTrue(all(upload.is_pending_evaluation(row) for row in before))
            restored = _Session()
            with patch.object(upload, 'evaluate_upload', return_value=perfect) as evaluate, \
                 patch.object(upload, '_put_blob_file', side_effect=AssertionError('no video replay')):
                upload.pump_pending(restored, max_retries=1,
                                    required_org_key='fixture-org', session_ids={'fixture-session'})
            self.assertEqual(evaluate.call_count, 2)
            self.assertEqual(restored.created, 0)
            after = [upload.load_sidecar('fixture-session', i) for i in range(2)]
            self.assertTrue(all(row['finalized'] for row in after))
            self.assertEqual([row['upload_id'] for row in after], [row['upload_id'] for row in before])

    def test_authentication_failure_does_not_hide_a_sibling_quality_rejection(self):
        with _fixture(count=2) as (_, paths, recorded):
            rejected = {'checks': [{'id': 'fixture', 'label': 'Fixture', 'status': 'fail', 'detail': 'Rejected'}]}
            with patch.object(upload, 'evaluate_upload', side_effect=[rejected, upload.UploadError(
                    'Expired access', status_code=401, phase='evaluate')]), \
                 patch.object(upload, 'delete_upload', side_effect=AssertionError('no compensation')), \
                 patch.object(upload, 'fail_upload', side_effect=AssertionError('no compensation')):
                result = _send(_Session(), paths, recorded, require_perfect=True, finalize=True)
            self.assertTrue(result.review_required)
            self.assertFalse(result.finalized)
            rows = [upload.load_sidecar('fixture-session', i) for i in range(2)]
            self.assertFalse(any(upload.is_pending_evaluation(row) for row in rows))

    def test_campaign_reconciliation_observes_authentication_then_same_receipt_confirmation(self):
        with _fixture() as (root, paths, recorded), \
             patch.object(config, 'DATA_DIR', root), patch.object(config, 'MEDIA_DATA_DIR', root):
            context = {'clip_uid': 'fixture-clip', 'registry_key': 'fixture-key', 'task_id': 'fixture-task'}
            expired = _Session()
            expired.finalize_status = 401
            _send(expired, paths, recorded, finalize=True, campaign_context=context)
            account = AccountSpec(expired.email, 'fixture-org')
            pending = campaign._reconcile_uploads(upload.list_sidecars(), account, context, 'fixture-task')
            self.assertFalse(pending['ok'])
            self.assertTrue(pending['authentication_required'])
            self.assertEqual(pending['session_id'], 'fixture-session')
            restored = _Session()
            upload.pump_pending(restored, max_retries=1, required_org_key='fixture-org')
            confirmed = campaign._reconcile_uploads(upload.list_sidecars(), account, context, 'fixture-task')
            self.assertTrue(confirmed['ok'])
            self.assertTrue(confirmed['finalized'])
            self.assertEqual(confirmed['session_id'], pending['session_id'])
            self.assertNotIn('authentication_required', confirmed)
            self.assertEqual(restored.created, 0)
