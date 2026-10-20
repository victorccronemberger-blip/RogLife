"""Distinguish issuer permission denial from explicit attestation rejection offline."""
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from moneymin import appcheck, readiness


class AppCheckPermissionDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        for target, name, value in (
            (appcheck.config, "ROOT", root),
            (appcheck.config, "DATA_DIR", root / "data"),
        ):
            setting = patch.object(target, name, value)
            setting.start()
            self.addCleanup(setting.stop)
        env = patch.dict(os.environ, {
            "FIREBASE_APP_CHECK_PROJECT_ID": "fixture-project",
            "FIREBASE_APP_CHECK_APP_ID": "1:123:ios:fixture",
            "FIREBASE_APP_CHECK_API_KEY": "fixture-key",
            "FIREBASE_APP_CHECK_DEBUG_TOKEN": "fixture-secret",
            "FIREBASE_APP_CHECK_BEARER": "",
        })
        env.start()
        self.addCleanup(env.stop)
        appcheck.clear_cache()
        self.addCleanup(appcheck.clear_cache)

    @staticmethod
    def error(status, message, firebase_status):
        return urllib.error.HTTPError(
            "https://issuer.example.invalid/fixture", status, "fixture", {},
            io.BytesIO(json.dumps({"error": {
                "message": message, "status": firebase_status,
            }}).encode()),
        )

    def test_permission_denial_is_not_proof_of_attestation_failure(self):
        for message in ("Permission denied.", "API key service blocked.", ""):
            with self.subTest(message=message):
                error = self.error(403, message, "PERMISSION_DENIED")
                self.assertEqual(appcheck._classify_exchange_http_error(error), "permission_denied")

    def test_explicit_attestation_rejection_and_quota_keep_their_categories(self):
        for status, message, firebase_status, expected in (
            (403, "App attestation failed.", "PERMISSION_DENIED", "attestation_failed"),
            (429, "Quota exceeded.", "RESOURCE_EXHAUSTED", "quota_exhausted"),
            (500, "Internal failure.", "INTERNAL", "exchange_failed"),
        ):
            with self.subTest(status=status):
                self.assertEqual(appcheck._classify_exchange_http_error(
                    self.error(status, message, firebase_status)), expected)

    def test_permission_denial_does_not_retry_after_cooldown(self):
        with patch.object(appcheck.tls, "urlopen", side_effect=self.error(
                403, "private-response-canary", "PERMISSION_DENIED")) as request, \
                patch.object(appcheck.time, "time", return_value=10000), \
                patch.object(appcheck.time, "monotonic", return_value=100), \
                patch.object(appcheck.logger, "warning") as warning:
            self.assertEqual(appcheck.get_app_check_header(), {})
            self.assertEqual(appcheck.last_mint_failure(), "permission_denied")
            diagnostic = appcheck.last_mint_diagnostic()
            self.assertEqual(diagnostic["http_status"], 403)
            self.assertEqual(diagnostic["firebase_status"], "PERMISSION_DENIED")
            self.assertEqual(diagnostic["message"],
                             "Firebase negou permissão na troca de App Check.")
            with patch.object(appcheck.time, "monotonic", return_value=10000):
                self.assertEqual(appcheck.get_app_check_header(), {})
            request.assert_called_once()
            state = appcheck._state_path().read_text(encoding="utf-8")
            self.assertNotIn("private-response-canary", state)
            self.assertNotIn("fixture-secret", state)
            self.assertNotIn("private-response-canary", str(warning.call_args))

    def test_permission_denial_is_restored_without_another_exchange(self):
        digest = appcheck._settings_digest(appcheck._settings())
        appcheck._save_persisted_state(digest, "permission_denied", 0)
        with patch.object(appcheck.tls, "urlopen") as request:
            self.assertEqual(appcheck.get_app_check_header(), {})
            self.assertEqual(appcheck.last_mint_failure(), "permission_denied")
            request.assert_not_called()

    def test_explicit_refresh_can_recheck_after_cooldown(self):
        digest = appcheck._settings_digest(appcheck._settings())
        appcheck._save_persisted_state(digest, "permission_denied", 0)
        with patch.object(appcheck, "_exchange", return_value=("fixture.jwt", 60)) as exchange:
            self.assertEqual(appcheck.mint_app_check_token(force_refresh=True), "fixture.jwt")
            exchange.assert_called_once()

    def test_exchange_error_exposes_only_structured_sanitized_diagnostic(self):
        details = self.error(
            403, "App attestation failed; private-response-canary fixture-secret",
            "PERMISSION_DENIED",
        )
        with patch.object(appcheck.tls, "urlopen", side_effect=details):
            with self.assertRaises(appcheck.AppCheckExchangeError) as raised:
                appcheck._exchange("fixture-project", "fixture-app", "fixture-key", "fixture-secret")
        self.assertEqual(raised.exception.category, "attestation_failed")
        self.assertEqual(raised.exception.http_status, 403)
        self.assertEqual(raised.exception.firebase_status, "PERMISSION_DENIED")
        self.assertNotIn("private-response-canary", str(raised.exception))
        self.assertNotIn("fixture-secret", str(raised.exception))

        with patch.object(appcheck.tls, "urlopen", side_effect=self.error(
                403, "App attestation failed; private-response-canary fixture-secret",
                "PERMISSION_DENIED")), \
                patch.object(appcheck.time, "time", return_value=10000), \
                patch.object(appcheck.time, "monotonic", return_value=100):
            self.assertEqual(appcheck.get_app_check_header(), {})
            diagnostic = appcheck.last_mint_diagnostic()
            self.assertEqual(diagnostic["category"], "attestation_failed")
            self.assertEqual(diagnostic["http_status"], 403)
            self.assertEqual(diagnostic["firebase_status"], "PERMISSION_DENIED")
            self.assertEqual(diagnostic["message"],
                             "Firebase recusou a atestação na troca de App Check.")
            state = appcheck._state_path().read_text(encoding="utf-8")
            self.assertIn('"http_status": 403', state)
            self.assertIn('"firebase_status": "PERMISSION_DENIED"', state)
            self.assertNotIn("private-response-canary", state)
            self.assertNotIn("fixture-secret", state)
            self.assertNotIn("issuer.example.invalid", state)

    def test_legacy_persisted_attestation_can_be_explicitly_rechecked_after_cooldown(self):
        digest = appcheck._settings_digest(appcheck._settings())
        appcheck._state_path().parent.mkdir(parents=True, exist_ok=True)
        appcheck._state_path().write_text(json.dumps({
            "settings_digest": digest,
            "failure": "attestation_failed",
            "retry_after_unix": 13600,
        }), encoding="utf-8")
        with patch.object(appcheck.time, "time", return_value=10000), \
                patch.object(appcheck.time, "monotonic", return_value=100), \
                patch.object(appcheck, "_exchange") as exchange:
            status = appcheck.app_check_status()
            self.assertEqual(status["diagnostic"]["http_status"], None)
            self.assertEqual(status["diagnostic"]["retry_after_seconds"], 3600)
            self.assertIsNone(appcheck.mint_app_check_token(force_refresh=True))
            exchange.assert_not_called()
            with patch.object(appcheck.time, "time", return_value=13601), \
                    patch.object(appcheck.time, "monotonic", return_value=3701), \
                    patch.object(appcheck, "_exchange", return_value=("fixture.jwt", 60)) as retry:
                self.assertEqual(appcheck.mint_app_check_token(force_refresh=True), "fixture.jwt")
                retry.assert_called_once()
                self.assertIsNone(appcheck.last_mint_diagnostic())

    def test_force_refresh_honors_cooldown_written_by_another_process(self):
        fresh_failure = {
            "failure": "permission_denied",
            "retry_after_unix": 10100,
            "http_status": 403,
            "firebase_status": "PERMISSION_DENIED",
            "message": "Firebase negou permissão na troca de App Check.",
        }
        with patch.object(appcheck.time, "time", return_value=10000), \
                patch.object(appcheck.time, "monotonic", return_value=100), \
                patch.object(appcheck, "_load_persisted_state",
                             side_effect=[None, fresh_failure]) as load, \
                patch.object(appcheck, "_exchange") as exchange:
            self.assertIsNone(appcheck.mint_app_check_token(force_refresh=True))
            self.assertEqual(load.call_count, 2)
            self.assertEqual(appcheck._last_failure, "permission_denied")
            self.assertEqual(appcheck._retry_at, 200)
            exchange.assert_not_called()

    def test_readiness_displays_permission_failure_without_issuing_token(self):
        with patch.object(appcheck, "last_mint_failure", return_value="permission_denied"), \
                patch.object(appcheck, "mint_app_check_token") as mint, \
                patch.object(readiness, "_binary_works", return_value=True), \
                patch.object(readiness, "_private_browser_present", return_value=True), \
                patch.object(readiness, "_valid_account_tokens", return_value=(1, 1)), \
                patch.object(readiness, "_aws_credentials_present", return_value=True), \
                patch.object(readiness.shutil, "disk_usage", return_value=type("Usage", (), {"free": 40 * 1024 ** 3})()):
            report = readiness.campaign_readiness("ego4d")
        check = next(item for item in report["checks"] if item["name"] == "App Check")
        self.assertEqual(check["status"], "warning")
        self.assertIn("permissão", check["detail"])
        mint.assert_not_called()
