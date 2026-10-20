"""Local HTTP contract for public USB diagnostics; no real device or credentials."""
from unittest.mock import patch
import unittest

from moneymin.integrations import android_diagnostics
from moneymin.web import server


class AndroidDeviceApiTests(unittest.TestCase):
    def setUp(self):
        self.client = server.create_app(for_testing=True).test_client()

    def test_diagnostic_returns_public_snapshot_without_running_other_integrations(self):
        expected = {"adb_available": True, "connection_state": "disconnected",
                    "devices": [], "selected_serial": None,
                    "device": {}, "root": {"state": "unknown"},
                    "collection": {"state": "unavailable", "message": "Conecte o celular."}}
        with patch.object(android_diagnostics, "snapshot", return_value=expected) as snapshot, \
             patch.object(server, "_integration_snapshot", side_effect=AssertionError("unrelated integration")):
            response = self.client.get("/api/integrations/android/device")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), expected)
        snapshot.assert_called_once_with(None)

    def test_selection_is_forwarded_only_to_public_diagnostics(self):
        with patch.object(android_diagnostics, "snapshot", return_value={}) as snapshot:
            response = self.client.get("/api/integrations/android/device?serial=fixture-serial")
        self.assertEqual(response.status_code, 200)
        snapshot.assert_called_once_with("fixture-serial")

    def test_extra_or_duplicate_parameters_are_rejected_before_any_device_command(self):
        with patch.object(android_diagnostics, "snapshot") as snapshot:
            for query in ("serial=a&serial=b", "command=id", "token=private-canary"):
                with self.subTest(query=query):
                    response = self.client.get("/api/integrations/android/device?" + query)
                    self.assertEqual(response.status_code, 400)
                    self.assertNotIn("private-canary", response.get_data(as_text=True))
        snapshot.assert_not_called()

    def test_route_is_read_only(self):
        with patch.object(android_diagnostics, "snapshot") as snapshot:
            self.assertEqual(self.client.post("/api/integrations/android/device", json={}).status_code, 405)
        snapshot.assert_not_called()
