"""Suspension at the real runner/engine boundary with inert provider fixtures."""
import json
from dataclasses import replace
import threading
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

import test_campaign_end_to_end as fixtures
from moneymin import campaign
from moneymin.web import runner, server


class CampaignAuthenticationPauseTests(unittest.TestCase):
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

    def await_pause(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            snapshot = self.fixture.instance.snapshot()
            if snapshot['pause_requested']:
                return snapshot
            time.sleep(0.01)
        self.fail('Authentication suspension was not published')

    def start_with_expired_access(self):
        fixture = self.fixture
        self.valid = False
        self.calls = []

        def send(item, account, *args, **kwargs):
            self.calls.append(account.email)
            if not self.valid:
                return {'email': account.email, 'ok': False, 'access_error': True,
                        'authentication_required': True, 'retryable': False,
                        'account_issue_code': 'authentication', 'http_status': 401,
                        'error': 'Fixture: expired access'}
            return {'email': account.email, 'ok': True, 'finalized': True}

        fixture.send.side_effect = send
        response = fixture.client.post('/api/campaigns', json=fixture.body)
        self.assertEqual(response.status_code, 200, response.get_json())
        return self.await_pause()

    def test_expired_access_saves_attempt_and_holds_queue_until_explicit_resume(self):
        snapshot = self.start_with_expired_access()
        fixture = self.fixture
        self.assertEqual(snapshot['stage'], 'Aguardando autenticação')
        self.assertEqual(snapshot['totals']['failed_sends'], 0)
        self.assertEqual(self.calls, fixture.emails[:1])
        history = json.loads(next(fixture.root.glob('campaign_*.json')).read_text(encoding='utf-8'))
        attempt = history['items'][0]['accounts'][0]
        self.assertTrue(attempt['authentication_required'])
        self.assertNotIn('excluded_from_campaign', attempt)
        fixture.cleanup.assert_not_called()
        fixture.mark.assert_not_called()
        self.valid = True  # External correction, not a token renewal operation.
        response = fixture.client.post('/api/campaigns/resume')
        self.assertEqual(response.status_code, 200, response.get_json())
        snapshot, history = fixture.finish()
        self.assertEqual(history['status'], 'done')
        self.assertEqual(snapshot['totals']['ok_sends'], 2)
        self.assertEqual(snapshot['totals']['failed_sends'], 0)
        self.assertEqual(self.calls.count(fixture.emails[0]), 2)
        self.assertEqual(self.calls.count(fixture.emails[1]), 1)

    def test_stop_releases_authentication_wait_without_starting_queued_account(self):
        self.start_with_expired_access()
        self.fixture.instance.stop()
        snapshot, history = self.fixture.finish()
        self.assertEqual(snapshot['state'], 'stopped')
        self.assertFalse(snapshot['pause_requested'])
        self.assertEqual(history['status'], 'stopped')
        self.assertEqual(self.calls, self.fixture.emails[:1])
        self.assertTrue(history['items'][0]['accounts'][0]['authentication_required'])
        self.fixture.cleanup.assert_not_called()

    def test_uncertain_authentication_result_halts_queue_for_receipt_review(self):
        fixture = self.fixture
        fixture.send.side_effect = lambda item, account, *a, **k: {
            'email': account.email, 'ok': False, 'authentication_required': True,
            'review_required': True, 'retryable': False, 'session_id': 'unconfirmed-create'}
        self.assertEqual(fixture.client.post('/api/campaigns', json=fixture.body).status_code, 200)
        snapshot, history = fixture.finish()
        self.assertEqual(snapshot['state'], 'stopped')
        self.assertEqual(snapshot['stage'], 'Revisão necessária')
        self.assertIn('Histórico', snapshot['current'])
        self.assertIn('não será repetido automaticamente', snapshot['current'])
        self.assertNotIn('Recuperação', snapshot['current'])
        self.assertFalse(snapshot['pause_requested'])
        self.assertTrue(snapshot['log_path'])
        self.assertEqual(fixture.send.call_count, 1)
        self.assertTrue(history['items'][0]['accounts'][0]['review_required'])
        fixture.mark.assert_not_called()
        fixture.cleanup.assert_not_called()

    def test_local_app_check_failure_halts_with_preserved_diagnosis(self):
        fixture = self.fixture
        fixture.send.side_effect = lambda item, account, *a, **k: {
            'email': account.email, 'ok': False, 'authentication_required': True,
            'retryable': False, 'blocked_reason': 'app_check',
            'appcheck_cause': 'device_unavailable',
            'appcheck_message': 'Dispositivo sem token Play Integrity',
            'error': 'App Check indisponível: Dispositivo sem token Play Integrity.'}
        self.assertEqual(fixture.client.post('/api/campaigns', json=fixture.body).status_code, 200)
        snapshot, history = fixture.finish()
        self.assertEqual(snapshot['state'], 'stopped')
        self.assertEqual(snapshot['stage'], 'App Check obrigatório')
        self.assertIn('device_unavailable', snapshot['current'])
        self.assertIn('Play Integrity', snapshot['current'])
        self.assertFalse(snapshot['pause_requested'])
        self.assertEqual(fixture.send.call_count, 1)
        attempt = history['items'][0]['accounts'][0]
        self.assertEqual(attempt['blocked_reason'], 'app_check')
        self.assertEqual(attempt['appcheck_cause'], 'device_unavailable')
        fixture.mark.assert_not_called()
        fixture.cleanup.assert_not_called()

    def test_failed_revalidation_keeps_the_suspended_receipt_identity(self):
        fixture = self.fixture
        attempts = []

        def send(item, account, *args, **kwargs):
            attempts.append(account.email)
            return {'email': account.email, 'ok': False, 'authentication_required': True,
                    'retryable': False,
                    **({'session_id': 'existing-receipt', 'org_key': 'org',
                        'uploads': ['existing-upload']} if len(attempts) == 1 else {})}

        fixture.send.side_effect = send
        self.assertEqual(fixture.client.post('/api/campaigns', json=fixture.body).status_code, 200)
        self.await_pause()
        # Let the queued account complete while revalidation of the suspended
        # identity still fails before reading its existing receipt.
        original_send = send

        def after_resume(item, account, *args, **kwargs):
            if account.email != fixture.emails[0]:
                return {'email': account.email, 'ok': True, 'finalized': True}
            return original_send(item, account, *args, **kwargs)

        fixture.send.side_effect = after_resume
        fixture.instance.resume()
        self.await_pause()
        fixture.instance.stop()
        _, history = fixture.finish()
        row = next(row for row in history['items'][0]['accounts'] if row['email'] == fixture.emails[0])
        self.assertEqual(attempts, [fixture.emails[0], fixture.emails[0]])
        self.assertEqual(row['session_id'], 'existing-receipt')
        self.assertEqual(row['uploads'], ['existing-upload'])

    def test_simulated_hours_expire_access_between_sends_without_losing_progress(self):
        fixture = self.fixture
        clock = {'now': 0, 'expires': 3600}
        calls = []

        def send(item, account, *args, **kwargs):
            calls.append(account.email)
            if clock['now'] >= clock['expires']:
                return {'email': account.email, 'ok': False, 'authentication_required': True,
                        'retryable': False, 'http_status': 401}
            clock['now'] += 7200  # Logical provider clock: a two-hour interval.
            return {'email': account.email, 'ok': True, 'finalized': True}

        fixture.send.side_effect = send
        self.assertEqual(fixture.client.post('/api/campaigns', json=fixture.body).status_code, 200)
        snapshot = self.await_pause()
        self.assertEqual(clock['now'], 7200)
        self.assertEqual(snapshot['totals']['ok_sends'], 1)
        self.assertEqual(snapshot['totals']['failed_sends'], 0)
        clock['expires'] = clock['now'] + 3600  # External correction.
        fixture.instance.resume()
        snapshot, history = fixture.finish()
        self.assertEqual(snapshot['totals']['ok_sends'], 2)
        self.assertEqual(history['status'], 'done')
        self.assertEqual(calls.count(fixture.emails[0]), 1)
        self.assertEqual(calls.count(fixture.emails[1]), 2)
        self.assertEqual(fixture.mark.call_count, 2)

    def test_recovery_failure_after_resume_keeps_original_receipt_in_history(self):
        fixture = self.fixture
        calls = []

        def send(item, account, *args, **kwargs):
            calls.append(account.email)
            if account.email != fixture.emails[0]:
                return {'email': account.email, 'ok': True, 'finalized': True}
            if calls.count(account.email) == 1:
                return {'email': account.email, 'ok': False, 'authentication_required': True,
                        'session_id': 'persisted-receipt', 'uploads': ['persisted-upload']}
            return {'email': account.email, 'ok': False, 'retryable': False,
                    'error': 'Fixture: receipt could not be read', 'review_required': True}

        fixture.send.side_effect = send
        self.assertEqual(fixture.client.post('/api/campaigns', json=fixture.body).status_code, 200)
        self.await_pause()
        fixture.instance.resume()
        _, history = fixture.finish()
        row = next(row for row in history['items'][0]['accounts'] if row['email'] == fixture.emails[0])
        self.assertFalse(row['ok'])
        self.assertEqual(row['session_id'], 'persisted-receipt')
        self.assertEqual(row['uploads'], ['persisted-upload'])
        self.assertTrue(row['review_required'])

    def test_authentication_resume_preserves_reserved_recording_time(self):
        fixture = self.fixture
        recorded = '2026-10-12T12:00:00.000Z'
        observed = []

        def send(item, account, *args, **kwargs):
            observed.append((account.email, kwargs['recorded_at']))
            if len(observed) == 1:
                return {'email': account.email, 'ok': False, 'authentication_required': True}
            return {'email': account.email, 'ok': True, 'finalized': True}

        fixture.send.side_effect = send
        with patch.object(campaign.recording_timeline, 'reserve',
                          return_value=SimpleNamespace(end_epoch=0, recorded_at=recorded)) as reserve, \
             patch.object(runner, 'run_campaign', side_effect=lambda cfg, **kw: campaign.run_campaign(
                 replace(cfg, realistic_timeline=True, allow_new_accounts=True,
                         shuffle_schedule=False, account_workers=1, account_gap_s=0), **kw)):
            self.assertEqual(fixture.client.post('/api/campaigns', json=fixture.body).status_code, 200)
            self.await_pause()
            fixture.instance.resume()
            _, history = fixture.finish()
        self.assertEqual(history['status'], 'done')
        self.assertEqual(reserve.call_count, len(fixture.emails))
        self.assertEqual([stamp for email, stamp in observed if email == fixture.emails[0]],
                         [recorded, recorded])

    def test_parallel_success_is_committed_before_authentication_suspension(self):
        fixture = self.fixture
        fixture.emails.append('queued@example.invalid')
        fixture.stack.enter_context(patch.object(server, '_list_accounts',
                                                return_value=[{'email': email} for email in fixture.emails]))
        second_started, release_second = threading.Event(), threading.Event()
        self.addCleanup(release_second.set)
        calls = []

        def send(item, account, *args, **kwargs):
            calls.append(account.email)
            if account.email == fixture.emails[0]:
                self.assertTrue(second_started.wait(3))
                return {'email': account.email, 'ok': False,
                        'authentication_required': True, 'retryable': False}
            if account.email == fixture.emails[1]:
                second_started.set()
                self.assertTrue(release_second.wait(5))
            return {'email': account.email, 'ok': True, 'finalized': True}

        fixture.send.side_effect = send
        with patch.object(runner, 'run_campaign', side_effect=lambda cfg, **kw: campaign.run_campaign(
                replace(cfg, realistic_timeline=False, allow_new_accounts=True,
                        shuffle_schedule=False, account_workers=2, account_gap_s=0), **kw)):
            response = fixture.client.post('/api/campaigns', json=fixture.body)
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertTrue(second_started.wait(3))
            deadline = time.monotonic() + 3
            history = None
            while time.monotonic() < deadline:
                paths = list(fixture.root.glob('campaign_*.json'))
                if paths:
                    history = json.loads(paths[0].read_text(encoding='utf-8'))
                    if history.get('items') and history['items'][0].get('accounts'):
                        break
                time.sleep(0.01)
            self.assertTrue(history['items'][0]['accounts'][0]['authentication_required'])
            self.assertFalse(fixture.instance.snapshot()['pause_requested'])
            self.assertNotIn(fixture.emails[2], calls)
            release_second.set()
            snapshot = self.await_pause()
            self.assertEqual(snapshot['totals']['ok_sends'], 1)
            fixture.instance.stop()
            _, history = fixture.finish()
        results = {row['email']: row for row in history['items'][0]['accounts']}
        self.assertTrue(results[fixture.emails[1]]['finalized'])
        self.assertNotIn(fixture.emails[2], results)
        self.assertEqual(fixture.mark.call_count, 1)
