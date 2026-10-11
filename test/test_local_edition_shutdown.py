"""The restored UI cannot kill workers still publishing local receipts."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from moneymin.web import server


class LocalEditionShutdownTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, QMONEY_LOCAL_API_TOKEN='local-fixture-token')
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = server.create_app(for_testing=True)
        self.client = self.app.test_client()
        self.headers = {'X-QMoney-Session': 'local-fixture-token'}
        for name in ('_withdraw_bulk_snapshot', '_payout_method_snapshot'):
            patcher = patch.object(server, name, return_value={'state': 'idle'})
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_shutdown_requires_desktop_authentication(self):
        self.assertEqual(self.client.post('/api/local/shutdown', json={}).status_code, 401)

    def test_idle_shutdown_is_ready_and_blocks_future_mutations(self):
        reply = self.client.post('/api/local/shutdown', json={}, headers=self.headers)
        self.assertEqual(reply.status_code, 200)
        self.assertIs(reply.json['ready'], True)
        blocked = self.client.post('/api/campaigns', json={}, headers=self.headers)
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(self.client.get('/api/health', headers=self.headers).status_code, 200)

    def test_live_worker_keeps_close_pending_until_thread_finishes(self):
        thread = SimpleNamespace(is_alive=lambda: True)
        with patch.object(server.RECOVERY, '_thread', thread):
            result = self.client.post('/api/local/shutdown', json={}, headers=self.headers)
            self.assertIs(result.json['ready'], False)
        result = self.client.post('/api/local/shutdown', json={}, headers=self.headers)
        self.assertIs(result.json['ready'], True)
