"""Offline App Check contract/regression tests; no installation cache or network."""
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, PropertyMock, patch

from moneymin import appcheck as a, minute_api as api
from moneymin.upload_types import UploadError


class AppCheckTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "FIREBASE_APP_CHECK_PROJECT_ID": "test-project",
            "FIREBASE_APP_CHECK_APP_ID": "1:123:android:abc",
            "FIREBASE_APP_CHECK_API_KEY": "fixture-key",
            "FIREBASE_APP_CHECK_DEBUG_TOKEN": "fixture-secret"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.data_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.data_dir.cleanup)
        self.data_dir_patch = patch.object(a.config, "DATA_DIR", Path(self.data_dir.name))
        self.data_dir_patch.start()
        self.addCleanup(self.data_dir_patch.stop)
        a.clear_cache()
        self.addCleanup(a.clear_cache)
        self.http = patch.object(a.tls, 'urlopen')
        self.open = self.http.start()
        self.addCleanup(self.http.stop)
        self.response = self.open.return_value.__enter__.return_value
        self.response.status = 200
        self.response.read.return_value = json.dumps({'token': 'fixture.jwt.token', 'ttl': '60s'}).encode()

    def test_http_contract(self):
        self.assertEqual(a.get_app_check_header(), {'X-Firebase-AppCheck': 'fixture.jwt.token'})
        req = self.open.call_args.args[0]
        self.assertEqual(req.full_url, 'https://firebaseappcheck.googleapis.com/v1/projects/test-project/apps/1%3A123%3Aandroid%3Aabc:exchangeDebugToken?key=fixture-key')
        self.assertEqual(req.method, 'POST')
        self.assertEqual(json.loads(req.data), {'debugToken': 'fixture-secret'})
        self.assertEqual(self.open.call_args.kwargs['timeout'], 10)

    def test_absent_or_partial_configuration_is_quiet(self):
        for key in ('FIREBASE_APP_CHECK_DEBUG_TOKEN', 'FIREBASE_APP_CHECK_APP_ID'):
            with patch.dict(os.environ, {key: ''}), patch.object(a.logger, 'warning') as warning:
                self.assertEqual(a.get_app_check_header(), {})
                self.assertFalse(a.is_app_check_configured())
                warning.assert_not_called()
        self.open.assert_not_called()

    def test_real_ttl_and_fractional_duration(self):
        self.response.read.return_value = b'{"token":"token","ttl":"60.5s"}'
        with patch.object(a.time, 'monotonic', return_value=100):
            a.mint_app_check_token()
            self.assertAlmostEqual(a._expires, 154.45)
        with patch.object(a.time, 'monotonic', return_value=154):
            a.mint_app_check_token()
            self.assertEqual(self.open.call_count, 1)
        with patch.object(a.time, 'monotonic', return_value=155):
            a.mint_app_check_token()
            self.assertEqual(self.open.call_count, 2)

    def test_invalid_payloads_are_not_cached(self):
        for payload in ({}, [], {'token': 'x', 'ttl': 'invalid'},
                        {'token': 'x', 'ttl': '0s'}, {'token': 'x\nBad: header', 'ttl': '60s'},
                        {'token': 123, 'ttl': '60s'}):
            with self.subTest(payload=payload):
                a.clear_cache()
                self.response.read.return_value = json.dumps(payload).encode()
                self.assertEqual(a.get_app_check_header(), {})
                self.assertIsNone(a._token)

    def test_http_error_backoff_and_no_secret_logging(self):
        self.open.side_effect = RuntimeError('fixture-secret fixture-key remote private body')
        with patch.object(a.time, 'monotonic', return_value=100), \
                patch.object(a.time, 'time', return_value=10000), \
                self.assertLogs(a.logger, 'WARNING') as log:
            self.assertIsNone(a.mint_app_check_token())
            self.assertIsNone(a.mint_app_check_token(force_refresh=True))
            self.assertEqual(self.open.call_count, 1)
            self.assertNotIn('fixture', ' '.join(log.output))
        with patch.object(a.time, 'monotonic', return_value=161), \
                patch.object(a.time, 'time', return_value=10061):
            a.mint_app_check_token()
            self.assertEqual(self.open.call_count, 2)

    def test_http_failure_status(self):
        self.response.status = 403
        self.assertEqual(a.get_app_check_header(), {})
        self.assertIsNone(a._token)

    def test_latency_cannot_extend_token_lifetime(self):
        with patch.object(a.time, 'monotonic', side_effect=[100, 170, 170]):
            self.assertIsNone(a.mint_app_check_token())

    def test_concurrent_calls_exchange_only_once(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: a.mint_app_check_token(), range(20)))
        self.assertEqual(set(results), {'fixture.jwt.token'})
        self.assertEqual(self.open.call_count, 1)

    def test_configuration_and_installation_separate_cache(self):
        a.mint_app_check_token()
        with patch.dict(os.environ, {'FIREBASE_APP_CHECK_DEBUG_TOKEN': 'another-secret'}):
            a.mint_app_check_token()
        with patch.object(a.config, 'ROOT', Path('other-installation')):
            a.mint_app_check_token()
        self.assertEqual(self.open.call_count, 3)

    def test_forced_refresh_and_memory_only_clear(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'APPDATA': root}):
            sentinel = Path(root)/'QMoney'/'appcheck_token.json'
            sentinel.parent.mkdir()
            sentinel.write_text('existing-user-file')
            a.mint_app_check_token()
            a.mint_app_check_token(force_refresh=True)
            a.clear_cache()
            self.assertEqual(sentinel.read_text(), 'existing-user-file')
            self.assertEqual(self.open.call_count, 2)
            self.assertIsNone(a._token)

    def test_explicit_rejection_detection(self):
        self.assertTrue(a.is_app_check_rejection('{"detail":{"error":"appcheck_required"}}'))
        self.assertTrue(a.is_app_check_rejection('{"detail":"Invalid App Check token"}'))
        for body in ('{"detail":"Request cannot be completed."}', '{"detail":"User account is disabled."}', '401', 'invalid'):
            self.assertFalse(a.is_app_check_rejection(body))
        self.assertTrue(a.is_app_check_blocked_reason('app_check'))
        self.assertTrue(a.is_app_check_blocked_reason('App_Check'))
        self.assertFalse(a.is_app_check_blocked_reason('rate_limit'))
        self.assertFalse(a.is_app_check_blocked_reason(None))

    def test_administrator_bearer_skips_debug_exchange(self):
        import base64
        import json as json_mod
        import time as time_mod

        header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').decode().rstrip("=")
        payload = base64.urlsafe_b64encode(json_mod.dumps({
            "exp": int(time_mod.time()) + 3600, "sub": "1:123:android:abc",
        }).encode()).decode().rstrip("=")
        token = f"{header}.{payload}.signature"
        with patch.dict(os.environ, {"FIREBASE_APP_CHECK_BEARER": token}):
            a.clear_cache()
            self.assertEqual(a.get_app_check_header(), {"X-Firebase-AppCheck": token})
            self.assertIsNone(a.last_mint_failure())
            status = a.app_check_status()
            self.assertTrue(status["configured"])
            self.assertTrue(status["token_available"])
        self.open.assert_not_called()

    def test_expired_administrator_bearer_is_rejected_without_exchange(self):
        import base64
        import json as json_mod
        import time as time_mod

        header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').decode().rstrip("=")
        payload = base64.urlsafe_b64encode(json_mod.dumps({
            "exp": int(time_mod.time()) - 10,
        }).encode()).decode().rstrip("=")
        token = f"{header}.{payload}.sig"
        with patch.dict(os.environ, {"FIREBASE_APP_CHECK_BEARER": token}):
            a.clear_cache()
            self.assertEqual(a.get_app_check_header(), {})
            self.assertEqual(a.last_mint_failure(), "invalid_response")
        self.open.assert_not_called()

    def test_nonfinite_administrator_bearer_expiration_is_rejected(self):
        import base64

        header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').decode().rstrip("=")
        payload = base64.urlsafe_b64encode(b'{"exp":1e999}').decode().rstrip("=")
        token = f"{header}.{payload}.signature"
        with patch.dict(os.environ, {"FIREBASE_APP_CHECK_BEARER": token}):
            a.clear_cache()
            self.assertEqual(a.get_app_check_header(), {})
            self.assertEqual(a.last_mint_failure(), "invalid_response")
        self.open.assert_not_called()

    def test_removed_administrator_bearer_selects_debug_exchange(self):
        import base64

        header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').decode().rstrip("=")
        payload = base64.urlsafe_b64encode(json.dumps({
            "exp": 13600, "sub": "1:123:android:abc",
        }).encode()).decode().rstrip("=")
        token = f"{header}.{payload}.fixture-signature"
        with patch.dict(os.environ, {"FIREBASE_APP_CHECK_BEARER": token}), \
                patch.object(a.time, "time", return_value=10000), \
                patch.object(a.time, "monotonic", return_value=100):
            self.assertEqual(a.get_app_check_header(), {"X-Firebase-AppCheck": token})
            self.open.assert_not_called()
            del os.environ["FIREBASE_APP_CHECK_BEARER"]
            self.assertEqual(a.get_app_check_header(), {
                "X-Firebase-AppCheck": "fixture.jwt.token"})
        self.open.assert_called_once()
        request = self.open.call_args.args[0]
        self.assertIn(":exchangeDebugToken?key=fixture-key", request.full_url)
        self.assertEqual(json.loads(request.data), {"debugToken": "fixture-secret"})

    def test_removed_near_expiry_bearer_cannot_escape_from_cache(self):
        import base64

        header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').decode().rstrip("=")
        payload = base64.urlsafe_b64encode(json.dumps({
            "exp": 10031, "sub": "1:123:android:abc",
        }).encode()).decode().rstrip("=")
        token = f"{header}.{payload}.fixture-signature"
        with patch.dict(os.environ, {"FIREBASE_APP_CHECK_BEARER": token}), \
                patch.object(a.time, "time", return_value=10000), \
                patch.object(a.time, "monotonic", return_value=100):
            self.assertEqual(a.mint_app_check_token(), token)
            del os.environ["FIREBASE_APP_CHECK_BEARER"]
            with patch.object(a.time, "time", return_value=10032), \
                    patch.object(a.time, "monotonic", return_value=132):
                self.assertFalse(a._bearer_still_valid(token))
                self.assertEqual(a.mint_app_check_token(), "fixture.jwt.token")
        self.open.assert_called_once()

    def test_removed_bearer_restores_persisted_attestation_denial(self):
        import base64

        header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').decode().rstrip("=")
        payload = base64.urlsafe_b64encode(json.dumps({
            "exp": 13600, "sub": "1:123:android:abc",
        }).encode()).decode().rstrip("=")
        token = f"{header}.{payload}.fixture-signature"
        with patch.dict(os.environ, {"FIREBASE_APP_CHECK_BEARER": ""}):
            denied_digest = a._settings_digest(a._settings())

        def persisted_state(digest):
            return ({"failure": "attestation_failed", "retry_after_unix": 13600}
                    if digest == denied_digest else None)

        with patch.dict(os.environ, {"FIREBASE_APP_CHECK_BEARER": token}), \
                patch.object(a.time, "time", return_value=10000), \
                patch.object(a.time, "monotonic", return_value=100), \
                patch.object(a, "_load_persisted_state", side_effect=persisted_state), \
                patch.object(a, "_exchange") as exchange:
            self.assertEqual(a.get_app_check_header(), {"X-Firebase-AppCheck": token})
            del os.environ["FIREBASE_APP_CHECK_BEARER"]
            self.assertEqual(a.get_app_check_header(), {})
            self.assertEqual(a.last_mint_failure(), "attestation_failed")
            self.assertEqual(a._retry_at, 3700)
            exchange.assert_not_called()
        self.open.assert_not_called()

    def test_attestation_failure_is_visible_without_emitting_header(self):
        import io
        import urllib.error

        error = urllib.error.HTTPError(
            'https://firebaseappcheck.googleapis.com/v1/example',
            403,
            'Forbidden',
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"code":403,"message":"App attestation failed.","status":"PERMISSION_DENIED"}}'),
        )
        self.open.side_effect = error
        self.assertIsNone(a.mint_app_check_token())
        self.assertEqual(a.last_mint_failure(), 'attestation_failed')
        self.assertEqual(a.get_app_check_header(), {})

    def test_quota_failure_category(self):
        import io
        import urllib.error

        error = urllib.error.HTTPError(
            'https://firebaseappcheck.googleapis.com/v1/example',
            429,
            'Too Many Requests',
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"code":429,"message":"Quota exceeded","status":"RESOURCE_EXHAUSTED"}}'),
        )
        self.open.side_effect = error
        with patch.object(a.time, 'monotonic', return_value=100.0):
            self.assertIsNone(a.mint_app_check_token())
            self.assertEqual(a.last_mint_failure(), 'quota_exhausted')
            self.assertGreaterEqual(a._retry_at, 170.0)
        # Ordinary callers wait out the per-minute Firebase debug-token window.
        with patch.object(a.time, 'monotonic', return_value=169.0):
            self.assertIsNone(a.mint_app_check_token())
            self.assertEqual(self.open.call_count, 1)

    def test_attestation_failure_does_not_retry_automatically_after_backoff(self):
        with patch.object(a, '_exchange', side_effect=RuntimeError('attestation_failed')) as exchange:
            with patch.object(a.time, 'monotonic', return_value=100), \
                    patch.object(a.time, 'time', return_value=10000):
                self.assertEqual(a.get_app_check_header(), {})
            for later in (161, 3600, 86400):
                with self.subTest(later=later), patch.object(a.time, 'monotonic', return_value=later):
                    self.assertEqual(a.get_app_check_header(), {})
                    self.assertEqual(a.last_mint_failure(), 'attestation_failed')
            self.assertEqual(exchange.call_count, 1)
            self.assertIsNone(a._token)

    def test_attestation_failure_new_settings_allow_a_new_exchange(self):
        with patch.object(a, '_exchange', side_effect=[RuntimeError('attestation_failed'), ('fresh-token', 60)]) as exchange:
            with patch.object(a.time, 'monotonic', return_value=100):
                self.assertEqual(a.get_app_check_header(), {})
            with patch.dict(os.environ, {'FIREBASE_APP_CHECK_DEBUG_TOKEN': 'new-authorized-fixture'}), \
                    patch.object(a.time, 'monotonic', return_value=101):
                self.assertEqual(a.get_app_check_header(), {'X-Firebase-AppCheck': 'fresh-token'})
                self.assertIsNone(a.last_mint_failure())
            self.assertEqual(exchange.call_count, 2)

    def test_attestation_failure_explicit_refresh_respects_cooldown(self):
        with patch.object(a, '_exchange', side_effect=[RuntimeError('attestation_failed'), ('fresh-token', 60)]) as exchange:
            with patch.object(a.time, 'monotonic', return_value=100):
                self.assertIsNone(a.mint_app_check_token())
                self.assertIsNone(a.mint_app_check_token(force_refresh=True))
                self.assertEqual(exchange.call_count, 1)
            # Attestation cooldown is one hour; explicit refresh waits that out.
            with patch.object(a.time, 'monotonic', return_value=100 + a._ATTESTATION_COOLDOWN_S + 1), \
                    patch.object(a.time, 'time', return_value=10000 + a._ATTESTATION_COOLDOWN_S + 1):
                self.assertEqual(a.mint_app_check_token(force_refresh=True), 'fresh-token')
                self.assertIsNone(a.last_mint_failure())
            self.assertEqual(exchange.call_count, 2)

    def test_status_snapshot_is_offline_and_reports_partial_config_and_cached_token(self):
        with tempfile.TemporaryDirectory() as root, \
                patch.object(a.config, "DATA_DIR", Path(root)), \
                patch.dict(os.environ, {"FIREBASE_APP_CHECK_DEBUG_TOKEN": ""}):
            status = a.app_check_status()
            self.assertFalse(status["configured"])
            self.assertTrue(status["configuration_present"])
            self.assertTrue(status["configuration_pending"])
            self.assertFalse(status["token_available"])
            self.assertFalse(status["paused"])
            self.open.assert_not_called()

            with patch.dict(os.environ, {"FIREBASE_APP_CHECK_DEBUG_TOKEN": "fixture-secret"}):
                before_mint = a.app_check_status()
                self.assertTrue(before_mint["configured"])
                self.assertFalse(before_mint["token_available"])
                self.open.assert_not_called()
                self.assertEqual(a.mint_app_check_token(), "fixture.jwt.token")
                after_mint = a.app_check_status()
                self.assertTrue(after_mint["token_available"])
                self.open.assert_called_once()

            (Path(root) / "appcheck_pause.flag").touch()
            paused = a.app_check_status()
            self.assertTrue(paused["paused"])
            self.assertFalse(paused["token_available"])
            self.assertEqual(a.get_app_check_header(), {})
            self.assertEqual(self.open.call_count, 1)

    def test_persisted_failure_diagnostic_restores_without_network(self):
        with tempfile.TemporaryDirectory() as root, \
                patch.object(a.config, "DATA_DIR", Path(root)), \
                patch.object(a.time, "time", return_value=10000), \
                patch.object(a.time, "monotonic", return_value=100):
            digest = a._settings_digest(a._settings())
            a._save_persisted_state(digest, "attestation_failed", 3600,
                                    http_status=403, firebase_status="PERMISSION_DENIED")
            a._identity = None
            a._token = None
            a._expires = 0
            a._last_failure = None
            a._last_diagnostic = None
            a._retry_at = 0
            status = a.app_check_status()
            self.assertEqual(status["failure"], "attestation_failed")
            self.assertEqual(status["diagnostic"]["category"], "attestation_failed")
            self.assertEqual(status["diagnostic"]["http_status"], 403)
            self.assertEqual(status["diagnostic"]["firebase_status"], "PERMISSION_DENIED")
            self.assertEqual(status["diagnostic"]["retry_after_seconds"], 3600)
            self.assertTrue(status["diagnostic"]["retry_requires_explicit"])
            self.assertEqual(a.last_mint_failure(), "attestation_failed")
            self.open.assert_not_called()

    def test_infinite_persisted_cooldown_is_ignored(self):
        with tempfile.TemporaryDirectory() as root, \
                patch.object(a.config, "DATA_DIR", Path(root)):
            digest = a._settings_digest(a._settings())
            a._state_path().parent.mkdir(parents=True, exist_ok=True)
            a._state_path().write_text(json.dumps({
                "settings_digest": digest,
                "failure": "attestation_failed",
                "retry_after_unix": float("inf"),
            }), encoding="utf-8")
            self.assertIsNone(a._load_persisted_state(digest))
            a._state_path().write_text(json.dumps({
                "settings_digest": digest,
                "failure": "attestation_failed",
                "retry_after_unix": 1e308,
            }), encoding="utf-8")
            self.assertIsNone(a._load_persisted_state(digest))

    def test_both_session_methods_preserve_appcheck_rejection_without_relogin(self):
        for detailed in (False, True):
            with self.subTest(detailed=detailed), patch.object(api.Session, 'id_token', new_callable=PropertyMock, return_value='firebase-token'):
                session = api.Session({'email': 'fixture@example.invalid', 'idToken': 'fixture'})
                body = '{"detail":{"error":"appcheck_required"}}'
                result = api.HttpResponse(401, body, {}) if detailed else (401, body)
                target = '_request_detailed' if detailed else '_request'
                with patch.object(api, target, return_value=result) as request, patch.object(session, 'refresh') as refresh, patch.object(session, '_relogin') as relogin:
                    actual = (session.request_detailed if detailed else session.request)('GET', '/api/v1/users/me')
                    self.assertEqual(actual, result)
                    self.assertEqual(request.call_args.kwargs['headers']['X-Firebase-AppCheck'], 'fixture.jwt.token')
                    refresh.assert_not_called()
                    relogin.assert_not_called()

    def test_success_without_configuration_for_both_methods(self):
        with patch.dict(os.environ, {'FIREBASE_APP_CHECK_DEBUG_TOKEN': ''}), patch.object(api.Session, 'id_token', new_callable=PropertyMock, return_value='firebase-token'):
            for detailed in (False, True):
                target = '_request_detailed' if detailed else '_request'
                result = api.HttpResponse(200, '{}', {}) if detailed else (200, '{}')
                with patch.object(api, target, return_value=result) as request:
                    s = api.Session({'email': 'fixture@example.invalid', 'idToken':'fixture'})
                    self.assertEqual((s.request_detailed if detailed else s.request)('GET','/api/v1/users/me'),result)
                    self.assertNotIn('X-Firebase-AppCheck',request.call_args.kwargs['headers'])
        self.open.assert_not_called()

    def test_readiness_reports_optional_attestation_failure_without_network(self):
        from moneymin import readiness

        with patch.object(a, '_exchange', side_effect=RuntimeError('attestation_failed')), \
                patch.object(a, '_save_persisted_state'):
            self.assertIsNone(a.mint_app_check_token())
        for tools_ready in (True, False):
            with self.subTest(tools_ready=tools_ready), \
                    patch.object(readiness, '_binary_works', return_value=tools_ready), \
                    patch.object(readiness, '_private_browser_present', return_value=True), \
                    patch.object(readiness, '_valid_account_tokens', return_value=(1, 1)), \
                    patch.object(readiness, '_aws_credentials_present', return_value=True), \
                    patch.object(readiness.shutil, 'disk_usage', return_value=type('U', (), {'free': 40 * 1024 ** 3})()):
                result = readiness.campaign_readiness('ego4d')
            app_check = next(item for item in result['checks'] if item['name'] == 'App Check')
            self.assertEqual(app_check['status'], 'warning')
            self.assertIn('atestação', app_check['detail'])
            self.assertEqual(result['ready'], tools_ready)
        self.open.assert_not_called()

    def test_ensure_app_check_fresh_renews_below_ten_minute_floor(self):
        stale = {
            "configured": True, "configuration_present": True, "token_available": False,
            "paused": False, "retry_after_seconds": 0,
            "device": {"enabled": True, "left_s": 500, "min_left_s": 600},
        }
        fresh = {**stale, "token_available": True,
                 "device": {"enabled": True, "left_s": 3500, "min_left_s": 600}}
        with patch.object(a, "app_check_status", side_effect=[stale, fresh]) as status, \
             patch.object(a, "mint_app_check_token", return_value="renewed.jwt") as mint:
            result = a.ensure_app_check_fresh()
        mint.assert_called_once_with(force_refresh=False)
        self.assertEqual(status.call_count, 2)
        self.assertTrue(result["token_available"])
        self.assertEqual(result["device"]["left_s"], 3500)

    def test_ensure_app_check_fresh_escalates_to_minute_restart(self):
        stale = {
            "configured": True, "configuration_present": True, "token_available": False,
            "paused": False, "retry_after_seconds": 0,
            "device": {"enabled": True, "left_s": 100, "min_left_s": 600},
        }
        still = {**stale}
        fresh = {**stale, "token_available": True,
                 "device": {"enabled": True, "left_s": 3500, "min_left_s": 600}}
        with patch.object(a, "app_check_status", side_effect=[stale, still, fresh]), \
             patch.object(a, "mint_app_check_token", return_value=None) as mint:
            result = a.ensure_app_check_fresh()
        self.assertEqual(
            [call.kwargs.get("force_refresh") for call in mint.call_args_list],
            [False, True],
        )
        self.assertTrue(result["token_available"])

    def test_ensure_escalates_when_mirrored_bearer_below_device_floor(self):
        mirrored = {
            "configured": True, "configuration_present": True, "token_available": True,
            "source": "bearer", "paused": False, "retry_after_seconds": 0,
            "device": {"enabled": True, "left_s": None, "min_left_s": 600},
        }
        fresh = {**mirrored, "source": "device",
                 "device": {"enabled": True, "left_s": 3500, "min_left_s": 600}}
        with patch.object(a, "app_check_status", side_effect=[mirrored, mirrored, fresh]), \
             patch.object(a, "_configured_bearer", return_value="mirror.jwt"), \
             patch.object(a, "_bearer_left_s", return_value=45.0), \
             patch.object(a, "mint_app_check_token", return_value="mirror.jwt") as mint:
            result = a.ensure_app_check_fresh()
        self.assertEqual(
            [call.kwargs.get("force_refresh") for call in mint.call_args_list],
            [False, True],
        )
        self.assertEqual(result["source"], "device")
        self.assertEqual(result["device"]["left_s"], 3500)

    def test_renew_escalates_when_mirrored_bearer_below_device_floor(self):
        mirrored = {
            "configured": True, "configuration_present": True, "token_available": True,
            "source": "bearer", "paused": False, "retry_after_seconds": 0,
            "device": {"enabled": True, "left_s": None, "min_left_s": 600},
        }
        fresh = {**mirrored, "source": "device",
                 "device": {"enabled": True, "left_s": 3500, "min_left_s": 600}}
        with patch("moneymin.roglife_appcheck.ensure_device_defaults_for_writes",
                   return_value={"enabled": True, "explicitly_disabled": False}), \
             patch.object(a, "app_check_status", side_effect=[mirrored, mirrored, fresh]), \
             patch.object(a, "_configured_bearer", return_value="mirror.jwt"), \
             patch.object(a, "_bearer_left_s", return_value=45.0), \
             patch.object(a, "mint_app_check_token", return_value="mirror.jwt") as mint:
            result = a.renew_app_check_for_batch()
        self.assertEqual(
            [(call.kwargs.get("force_refresh"), call.kwargs.get("bypass_live_session"))
             for call in mint.call_args_list],
            [(False, True), (True, True)],
        )
        self.assertEqual(result["device"]["left_s"], 3500)

    def test_bearer_left_reports_expiry_delta_and_rejects_bad_shape(self):
        import base64
        from moneymin.mobile.appcheck import jwt as jwt_module

        segment = base64.urlsafe_b64encode(json.dumps({"exp": 1000}).encode()).rstrip(b"=").decode()
        token = f"header.{segment}.signature"
        self.assertEqual(jwt_module.bearer_left_s(token, clock=lambda: 940.0), 60.0)
        self.assertTrue(jwt_module.bearer_still_valid(token, clock=lambda: 940.0))
        self.assertFalse(jwt_module.bearer_still_valid(token, clock=lambda: 980.0))
        self.assertIsNone(jwt_module.bearer_left_s("not-a-jwt", clock=lambda: 0.0))

    def test_ensure_ignores_firebase_cooldown_in_device_mode(self):
        stale = {
            "configured": True, "configuration_present": True, "token_available": False,
            "paused": False, "retry_after_seconds": 3600,
            "device": {"enabled": True, "left_s": None, "min_left_s": 600},
        }
        fresh = {**stale, "token_available": True, "retry_after_seconds": 0,
                 "device": {"enabled": True, "left_s": 3500, "min_left_s": 600}}
        with patch.object(a, "app_check_status", side_effect=[stale, fresh]), \
             patch.object(a, "mint_app_check_token", return_value="device.jwt") as mint:
            result = a.ensure_app_check_fresh()
        mint.assert_called_once_with(force_refresh=False)
        self.assertTrue(result["token_available"])

    def test_ensure_app_check_fresh_skips_when_above_floor(self):
        ready = {
            "configured": True, "configuration_present": True, "token_available": True,
            "paused": False, "retry_after_seconds": 0,
            "device": {"enabled": True, "left_s": 1200, "min_left_s": 600},
        }
        with patch.object(a, "app_check_status", return_value=ready), \
             patch.object(a, "mint_app_check_token") as mint:
            self.assertEqual(a.ensure_app_check_fresh(), ready)
        mint.assert_not_called()

    def test_renew_app_check_for_batch_always_bypasses_live_session(self):
        ready = {
            "configured": True, "configuration_present": True, "token_available": True,
            "paused": False, "retry_after_seconds": 0,
            "device": {"enabled": True, "left_s": 3500, "min_left_s": 600},
        }
        # Status still shows a long-lived token; batch renew must still mint
        # with bypass so each multi-account clip re-reads the phone.
        with patch("moneymin.roglife_appcheck.ensure_device_defaults_for_writes",
                   return_value={"enabled": True, "explicitly_disabled": False}), \
             patch.object(a, "app_check_status", side_effect=[ready, ready]) as status, \
             patch.object(a, "mint_app_check_token", return_value="batch.jwt") as mint:
            result = a.renew_app_check_for_batch()
        mint.assert_called_once_with(force_refresh=False, bypass_live_session=True)
        self.assertEqual(status.call_count, 2)
        self.assertTrue(result["token_available"])

    def test_renew_app_check_for_batch_escalates_when_soft_misses(self):
        unavailable = {
            "configured": True, "configuration_present": True, "token_available": False,
            "paused": False, "retry_after_seconds": 0,
            "device": {"enabled": True, "left_s": None, "min_left_s": 600},
        }
        available = {**unavailable, "token_available": True,
                     "device": {"enabled": True, "left_s": 3500, "min_left_s": 600}}
        with patch("moneymin.roglife_appcheck.ensure_device_defaults_for_writes",
                   return_value={"enabled": True, "explicitly_disabled": False}), \
             patch.object(a, "app_check_status",
                          side_effect=[unavailable, unavailable, available]), \
             patch.object(a, "mint_app_check_token", return_value=None) as mint:
            result = a.renew_app_check_for_batch()
        self.assertEqual(
            [(c.kwargs.get("force_refresh"), c.kwargs.get("bypass_live_session"))
             for c in mint.call_args_list],
            [(False, True), (True, True)],
        )
        self.assertTrue(result["token_available"])

    def test_write_without_appcheck_raises_local_error_with_operation_phase(self):
        # Writes require App Check locally before the network call. Phase follows
        # the operation contract (create vs finalize), never a blanket "create".
        cases = (
            ('POST', '/api/v1/uploads', 'create'),
            ('POST', '/api/v1/organizations/org/sessions/sid/finalize', 'finalize'),
            ('POST', '/api/v1/storage/sas/blobs', 'sas'),
            ('PATCH', '/api/v1/uploads/uid/complete', 'complete'),
        )
        for detailed in (False, True):
            for method, path, phase in cases:
                with self.subTest(detailed=detailed, method=method, path=path):
                    a.clear_cache()
                    target = '_request_detailed' if detailed else '_request'
                    with patch.dict(os.environ, {'FIREBASE_APP_CHECK_DEBUG_TOKEN': ''}), \
                            patch.object(api.Session, 'id_token', new_callable=PropertyMock, return_value='firebase-token'), \
                            patch.object(api.Session, '_check_write_policy'), \
                            patch.object(api, target) as request, \
                            patch.object(a, '_exchange') as exchange:
                        session = api.Session({'email': 'fixture@example.invalid', 'idToken': 'firebase-token'})
                        with self.assertRaises(UploadError) as raised:
                            (session.request_detailed if detailed else session.request)(method, path, {})
                        error = raised.exception
                        self.assertEqual(error.phase, phase)
                        self.assertEqual(error.blocked_reason, 'app_check')
                        self.assertTrue(error.appcheck_cause)
                        self.assertTrue(error.appcheck_message)
                        self.assertFalse(error.retryable)
                        self.assertTrue(error.request_not_sent)
                        request.assert_not_called()
                        exchange.assert_not_called()

    def test_get_without_appcheck_still_reaches_network(self):
        a.clear_cache()
        with patch.dict(os.environ, {'FIREBASE_APP_CHECK_DEBUG_TOKEN': ''}), \
                patch.object(api.Session, 'id_token', new_callable=PropertyMock, return_value='firebase-token'), \
                patch.object(api, '_request', return_value=(200, '{}')) as request:
            session = api.Session({'email': 'fixture@example.invalid', 'idToken': 'firebase-token'})
            self.assertEqual(session.request('GET', '/api/v1/users/me'), (200, '{}'))
            self.assertNotIn('X-Firebase-AppCheck', request.call_args.kwargs['headers'])
            self.assertEqual(request.call_args.kwargs['headers']['Authorization'], 'Bearer firebase-token')

    def test_write_gate_renews_from_device_before_refusing(self):
        with patch.object(api, "_device_appcheck_renewable", return_value=True), \
             patch.object(a, "get_app_check_header",
                          side_effect=[{}, {"X-Firebase-AppCheck": "renewed.jwt"}]) as header, \
             patch.object(a, "ensure_app_check_fresh",
                          return_value={"token_available": True}) as renew:
            result = api.require_app_check_for_write("POST", "/api/v1/uploads")
        renew.assert_called_once_with()
        self.assertEqual(result, {"X-Firebase-AppCheck": "renewed.jwt"})
        self.assertEqual(header.call_count, 2)

    def test_write_gate_does_not_renew_when_device_mode_off(self):
        with patch.object(api, "_device_appcheck_renewable", return_value=False), \
             patch.object(a, "get_app_check_header", return_value={}), \
             patch.object(a, "ensure_app_check_fresh") as renew, \
             patch.object(a, "app_check_status", return_value={
                 "diagnostic": {"category": "not_configured", "message": "sem credencial"}}):
            with self.assertRaises(UploadError) as raised:
                api.require_app_check_for_write("POST", "/api/v1/uploads")
        renew.assert_not_called()
        self.assertEqual(raised.exception.blocked_reason, "app_check")

    def test_write_gate_still_refuses_when_device_renew_fails(self):
        with patch.object(api, "_device_appcheck_renewable", return_value=True), \
             patch.object(a, "get_app_check_header", return_value={}), \
             patch.object(a, "ensure_app_check_fresh",
                          return_value={"token_available": False}), \
             patch.object(a, "app_check_status", return_value={
                 "diagnostic": {"category": "device_capture_failed",
                                "message": "celular indisponível"}}):
            with self.assertRaises(UploadError) as raised:
                api.require_app_check_for_write("POST", "/api/v1/uploads")
        self.assertEqual(raised.exception.appcheck_cause, "device_capture_failed")


if __name__ == '__main__':
    unittest.main()
