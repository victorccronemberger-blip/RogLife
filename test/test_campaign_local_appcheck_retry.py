"""Local credential failure must not replay costly account preparation."""
import unittest
from unittest.mock import Mock, patch

import test_campaign_end_to_end as fixtures
from moneymin import appcheck, campaign
from moneymin.web import runner


class LocalAppCheckRetryTests(unittest.TestCase):
    def test_real_account_gate_stops_retry_loop_and_preserves_media(self):
        fixture = fixtures.CampaignEndToEndTests(
            'test_success_matches_polling_and_persisted_history')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)

        def stop_worker():
            fixture.instance.stop()
            if fixture.instance._thread:
                fixture.instance._thread.join(5)
        self.addCleanup(stop_worker)
        session = Mock(_live=True)

        def send(_item, account, *args, **kwargs):
            return fixtures.REAL_UPLOAD_TO_ACCOUNT(
                {}, account, 'task', 30, True, True,
                session=session, recover_pending=False)

        fixture.send.side_effect = send
        available = {'token_available': True, 'configured': True, 'source': 'fixture',
                     'device': {'left_s': 3600, 'enabled': True, 'min_left_s': 600}}
        unavailable = {'token_available': False, 'diagnostic': {
            'category': 'device_capture_failed',
            'message': 'Fixture: credential unavailable'}}
        with patch.object(appcheck, 'renew_app_check_for_batch',
                          return_value=available), \
             patch.object(appcheck, 'ensure_app_check_fresh',
                          side_effect=[available, unavailable]) as fresh, \
             patch.object(campaign.org_policy, 'account_kind', return_value='other'), \
             patch.object(campaign, 'upload_session') as upload:
            response = fixture.client.post('/api/campaigns', json={
                **fixture.body, 'account_max_attempts': 5})
            self.assertEqual(response.status_code, 200, response.get_json())
            state, history = fixture.finish()
        self.assertEqual(fixture.send.call_count, 1)
        # Admission sees a usable token; the account gate then refuses once.
        self.assertEqual(fresh.call_count, 2)
        upload.assert_not_called()
        fixture.cleanup.assert_not_called()
        fixture.mark.assert_not_called()
        self.assertEqual(state['state'], 'stopped')
        self.assertEqual(state['stage'], 'App Check obrigatório')
        self.assertIn('device_capture_failed', state['current'])
        attempt = history['items'][0]['accounts'][0]
        self.assertEqual(attempt['campaign_attempts'], 1)
        self.assertTrue(attempt['request_not_sent'])
        self.assertFalse(attempt['retryable'])
        self.assertFalse(attempt['review_required'])
        self.assertEqual(attempt['appcheck_cause'], 'device_capture_failed')

    def test_historical_capture_error_is_not_reported_as_video_failure(self):
        for error in (
            'App Check indisponível: device_capture_failed; próximo vídeo',
            'lote incompleto após todas as tentativas: App Check indisponível: private-diagnostic',
        ):
            with self.subTest(error=error):
                message = runner.friendly_campaign_error(error)
                self.assertIn('App Check indisponível', message)
                self.assertNotIn('Não foi possível preparar', message)
                self.assertNotIn('envio continua sem confirmação', message)
                self.assertNotIn('private-diagnostic', message)
