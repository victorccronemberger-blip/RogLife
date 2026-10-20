"""Identified uncertain clips reserve content, without requiring account recovery."""
import json
import unittest
from unittest.mock import patch

from moneymin import appcheck, campaign, recovery, upload
import test_campaign_end_to_end as campaign_fixtures
from auth_fixtures import configure_app_check


class PendingCampaignAdmissionTests(unittest.TestCase):
    def setUp(self):
        configure_app_check(self)
        fixture = campaign_fixtures.CampaignEndToEndTests('test_success_matches_polling_and_persisted_history')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.journals = fixture.root / 'sidecars'
        self.journals.mkdir()
        fixture.stack.enter_context(patch.object(upload, 'sidecars_dir', return_value=self.journals))
        # Authorization availability is an independent boundary. The fixtures
        # below test pending-content admission, not token renewal or providers.
        fixture.stack.enter_context(patch.object(appcheck, 'app_check_status', return_value={'token_available': True}))
        self.pump = fixture.stack.enter_context(patch.object(
            campaign, '_pump_account_pending', side_effect=AssertionError('no implicit recovery')))
        self.resume = fixture.stack.enter_context(patch.object(
            recovery, 'resume_account', side_effect=AssertionError('no account recovery')))

    def pending(self, *, legacy=False, uid='clip'):
        fixture = self.fixture
        row = {
            'session_id': 'pending-fixture', 'account_email': fixture.emails[0],
            'org_key': 'org', 'task_id': 'task', 'chunk_index': 0, 'expected_chunk_count': 1,
            'state': 'creating' if legacy else 'failed',
            'phase': 'sidecar_validated' if legacy else 'create',
            'finalized': False, 'finalize_requested': True,
            'recorded_at': '2026-10-13T00:00:00.000Z',
            'local_video_path': str(fixture.root / 'old.mp4'),
        }
        context = {'registry_key': 'minute|task|Furniture Assembly', 'clip_uid': uid, 'task_id': 'task'}
        if legacy:
            history = {'items': [{**context, 'accounts': [{
                'email': fixture.emails[0], 'session_id': row['session_id']}]}]}
            (fixture.root / 'campaign_legacy.json').write_text(json.dumps(history), encoding='utf-8')
        else:
            row.update(create_attempted=True, campaign_context=context)
        path = self.journals / 'pending-fixture.json'
        path.write_text(json.dumps(row), encoding='utf-8')
        return path

    def finish(self):
        fixture = self.fixture
        fixture.instance._thread.join(5)
        self.assertFalse(fixture.instance._thread.is_alive())
        self.pump.assert_not_called()
        self.resume.assert_not_called()
        self.assertTrue(all(call.kwargs['recover_pending'] is False for call in fixture.send.call_args_list))
        return fixture.client.get('/api/campaigns/current').get_json()

    def test_legacy_identified_needs_review_allows_another_clip_without_recovery(self):
        fixture = self.fixture
        fixture.emails[:] = fixture.emails[:1]
        path = self.pending(legacy=True)
        before = path.read_bytes()
        item = recovery.snapshot()['items'][0]
        self.assertEqual(item['status'], 'needs_review')
        self.assertFalse(item['can_resume'])
        self.assertFalse(item['blocks_campaign'])
        candidates = [{'clip_uid': uid, 'dur_s': 300, 'source': 'ego4d'} for uid in ('clip', 'fresh')]
        with patch.object(campaign, '_compatible_task_clips', return_value=candidates), \
             patch.object(campaign, '_ego_clip_inputs', side_effect=lambda clip: (clip, {})):
            review = fixture.client.post('/api/campaigns/preflight', json={
                **fixture.body, 'include_clip_plan': True}).get_json()
            self.assertTrue(review['ok'], review)
            blocked = next(clip for clip in review['clip_plan'] if clip['clip_uid'] == 'clip')
            self.assertEqual(blocked['eligible_accounts'], [])
            self.assertEqual(blocked['pending_accounts'], fixture.emails)
            response = fixture.client.post('/api/campaigns', json=fixture.body)
            self.assertEqual(response.status_code, 200, response.get_json())
            snapshot = self.finish()
        self.assertEqual([call.args[0]['clip_uid'] for call in fixture.prepare.call_args_list], ['fresh'])
        self.assertEqual(snapshot['totals']['ok_sends'], 1)
        self.assertEqual(path.read_bytes(), before)

    def test_uncertain_create_reserves_only_its_owner_while_other_account_can_use_clip(self):
        fixture = self.fixture
        path = self.pending()
        before = path.read_bytes()
        item = recovery.snapshot()['items'][0]
        self.assertEqual(item['status'], 'needs_review')
        self.assertFalse(item['can_resume'])
        self.assertFalse(item['blocks_campaign'])
        self.assertEqual(recovery.campaign_exclusions([item]), {'clip': [fixture.emails[0]]})
        review = fixture.client.post('/api/campaigns/preflight', json={
            **fixture.body, 'include_clip_plan': True}).get_json()
        candidate = next(clip for clip in review['clip_plan'] if clip['clip_uid'] == 'clip')
        self.assertEqual(candidate['eligible_accounts'], fixture.emails[1:])
        self.assertEqual(candidate['pending_accounts'], fixture.emails[:1])
        response = fixture.client.post('/api/campaigns', json=fixture.body)
        self.assertEqual(response.status_code, 200, response.get_json())
        snapshot = self.finish()
        self.assertEqual([call.args[1].email for call in fixture.send.call_args_list], fixture.emails[1:])
        self.assertEqual(snapshot['totals']['ok_sends'], 1)
        self.assertEqual(path.read_bytes(), before)

    def test_unidentified_pending_content_still_blocks_before_preparation(self):
        fixture = self.fixture
        path = self.pending()
        row = json.loads(path.read_text(encoding='utf-8'))
        row.pop('campaign_context')
        path.write_text(json.dumps(row), encoding='utf-8')
        response = fixture.client.post('/api/campaigns', json=fixture.body)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()['error_code'], 'recovery_unidentified')
        fixture.prepare.assert_not_called()
        fixture.send.assert_not_called()
        self.pump.assert_not_called()


if __name__ == '__main__':
    unittest.main()
