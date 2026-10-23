"""Only actual prior CREATE refusals can stop an otherwise optional integration."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from moneymin import appcheck, config, upload
from moneymin.campaign_types import AccountSpec
from moneymin.delivery import admission


class CreateAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.account = AccountSpec("fixture@example.invalid", "org-a")
        self.refusal = {"account_email": self.account.email, "org_key": self.account.org_key,
                        "state": "failed", "phase": "create", "upload_id": None,
                        "updated_at": "2026-10-10T12:00:00.000Z",
                        "error": "POST /uploads falhou (429): serviço recusou [bloqueio: app_check]"}
        # Admission activates device defaults; keep unit tests off the phone/ADB.
        self._defaults = patch(
            "moneymin.roglife_appcheck.provider.ensure_device_defaults_for_writes",
            return_value={"enabled": True, "explicitly_disabled": False,
                          "activated_source": False, "activated_serial": False,
                          "serial": None})
        self._mode = patch(
            "moneymin.roglife_appcheck.provider.device_mode_enabled", return_value=True)
        # Device-mode admission also requires a usable physical identity snapshot.
        self._identity = patch(
            "moneymin.roglife_appcheck.provider.physical_identity",
            return_value={
                "serial": "FIXTURE-PHONE",
                "device_id": "android.ssaid:fixturephone",
                "model": "SM-S901E",
                "system_name": "Android",
                "system_version": "16",
                "sdk_int": 36,
                "uptime_ns": 10_000_000_000,
                "app_version": "1.29.0",
                "collected_at_unix": 1_700_000_000,
            })
        # Supply the independent public package observation, not a gate bypass.
        self._version = patch(
            "moneymin.mobile.minute_version.observe_installed_version",
            return_value={"serial": "FIXTURE-PHONE", "package": "com.bakerdata.minute",
                          "version_name": "1.29.0", "version_code": 1004038})
        self._defaults.start()
        self._mode.start()
        self._identity.start()
        self._version.start()
        self.addCleanup(self._defaults.stop)
        self.addCleanup(self._mode.stop)
        self.addCleanup(self._identity.stop)
        self.addCleanup(self._version.stop)

    def test_exact_create_refusal_scoped_to_account_and_org(self):
        self.assertEqual(admission.rejected_create_owners([self.account], [self.refusal]),
                         {self.account.email})
        for changes in ({"org_key": "org-b"}, {"account_email": "other@example.invalid"},
                        {"phase": "complete"}, {"state": "done"},
                        {"error": "HTTP 429 documentação app_check"},
                        {"error": "POST /uploads falhou (429): [bloqueio: rate_limit]"},
                        {"updated_at": "invalid"}):
            with self.subTest(changes=changes):
                self.assertEqual(admission.rejected_create_owners([self.account],
                                 [{**self.refusal, **changes}]), set())

    def test_later_remote_receipt_clears_previous_refusal_regardless_of_list_order(self):
        receipt = {**self.refusal, "upload_id": "remote-upload-id", "state": "transport",
                   "phase": "transport", "updated_at": "2026-10-10T12:01:00Z", "error": None}
        for rows in ([receipt, self.refusal], [self.refusal, receipt]):
            self.assertEqual(admission.rejected_create_owners([self.account], rows), set())
        receipt["updated_at"] = "2026-10-10T11:59:00Z"
        self.assertEqual(admission.rejected_create_owners([self.account], [receipt, self.refusal]),
                         {self.account.email})

    def test_admission_renews_before_consulting_prior_refusal(self):
        rows = [deepcopy(self.refusal)]
        renewed = {"token_available": True, "configured": True, "source": "device"}
        with patch.object(appcheck, "ensure_app_check_fresh", return_value=renewed) as ensure, \
             patch.object(upload, "list_sidecars") as read:
            blocker = admission.campaign_create_blocker([self.account])
        self.assertIsNone(blocker)
        ensure.assert_called_once_with()
        read.assert_not_called()
        self.assertEqual(rows, [self.refusal])

    def test_known_remote_refusal_blocks_only_after_renewal_still_unavailable(self):
        rows = [deepcopy(self.refusal)]
        unavailable = {"token_available": False, "configured": True, "source": None}
        with patch.object(appcheck, "ensure_app_check_fresh", return_value=unavailable) as ensure, \
             patch.object(admission, "_has_journal_files", return_value=True), \
             patch.object(upload, "list_sidecars", return_value=rows):
            blocker = admission.campaign_create_blocker([self.account])
        self.assertIn("último CREATE", blocker)
        self.assertIn("nenhum preparo", blocker)
        ensure.assert_called_once_with()
        self.assertEqual(rows, [self.refusal])

    def test_unavailable_token_blocks_before_expensive_preparation(self):
        status = {"token_available": False, "configured": True, "device": {"serial": "RQCT804KN2N"},
                  "diagnostic": {"category": "device_capture_failed",
                                 "message": "Fixture: telefone sem token"}}
        with patch.object(appcheck, "ensure_app_check_fresh", return_value=status), \
             patch.object(admission, "_has_journal_files", return_value=False), \
             patch("moneymin.roglife_appcheck.provider.ensure_device_defaults_for_writes",
                   return_value={"enabled": True, "explicitly_disabled": False,
                                 "activated_source": False, "activated_serial": False,
                                 "serial": "RQCT804KN2N"}), \
             patch("moneymin.roglife_appcheck.provider.device_mode_enabled", return_value=True), \
             patch.object(upload, "list_sidecars") as read:
            blocker = admission.campaign_create_blocker([self.account])
        self.assertIn("escrita no Minute exige", blocker)
        self.assertIn("nenhum preparo", blocker.casefold())
        self.assertIn("device_capture_failed", blocker)
        self.assertIn("RQCT804KN2N", blocker)
        read.assert_not_called()

    def test_explicit_device_disable_blocks_with_mode_guidance(self):
        unavailable = {"token_available": False, "configured": False, "device": {"enabled": False}}
        with patch.object(appcheck, "ensure_app_check_fresh", return_value=unavailable), \
             patch("moneymin.roglife_appcheck.provider.ensure_device_defaults_for_writes",
                   return_value={"enabled": False, "explicitly_disabled": True,
                                 "activated_source": False, "activated_serial": False,
                                 "serial": None}), \
             patch("moneymin.roglife_appcheck.provider.device_mode_enabled", return_value=False), \
             patch.object(upload, "list_sidecars") as read:
            blocker = admission.campaign_create_blocker([self.account])
        self.assertIn("desligado", blocker.casefold())
        self.assertIn("QMONEY_APPCHECK_SOURCE", blocker)
        read.assert_not_called()

    def test_available_token_does_not_claim_remote_acceptance(self):
        with patch.object(appcheck, "ensure_app_check_fresh",
                          return_value={"token_available": True}) as ensure, \
             patch.object(upload, "list_sidecars") as read:
            self.assertIsNone(admission.campaign_create_blocker([self.account]))
        ensure.assert_called_once_with()
        read.assert_not_called()

    def test_empty_profile_probe_blocks_without_locks_directories_or_receipts(self):
        unavailable = {"token_available": False, "configured": False, "device": {}}
        with TemporaryDirectory() as folder, \
             patch.multiple(config, DATA_DIR=Path(folder) / "data", MEDIA_DATA_DIR=Path(folder) / "media"), \
             patch.object(appcheck, "ensure_app_check_fresh", return_value=unavailable), \
             patch("moneymin.roglife_appcheck.provider.ensure_device_defaults_for_writes",
                   return_value={"enabled": True, "explicitly_disabled": False,
                                 "activated_source": True, "activated_serial": False,
                                 "serial": None}), \
             patch("moneymin.roglife_appcheck.provider.device_mode_enabled", return_value=True), \
             patch.object(upload, "list_sidecars") as read:
            blocker = admission.campaign_create_blocker([self.account])
            self.assertIn("App Check", blocker)
            self.assertEqual(list(Path(folder).iterdir()), [])
            read.assert_not_called()
