"""Live ADB JWT session vs identity-only disk profile; no phone or credentials."""
import json
import os
import runpy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from moneymin.roglife_appcheck import provider


class DeviceCacheTests(unittest.TestCase):
    def setUp(self):
        provider.clear_live_token()

    def tearDown(self):
        provider.clear_live_token()

    def test_windows_cli_and_desktop_resolve_same_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            local = Path(temp) / "local"
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local), "QMONEY_USER_ROOT": ""}), \
                 patch.object(provider.sys, "platform", "win32"):
                cli_path = provider.cache_path()
                with patch.dict(os.environ, {"QMONEY_USER_ROOT": str(local / "QMoney")}):
                    self.assertEqual(cli_path, provider.cache_path())
                self.assertEqual(cli_path, local / "QMoney/data/appcheck_device.json")

    def test_capture_entrypoint_does_not_redirect_to_temporary_profile(self):
        script = Path(__file__).resolve().parents[1] / "app/examples/refresh_device_appcheck.py"
        with tempfile.TemporaryDirectory() as temp, \
             patch.dict(os.environ, {"LOCALAPPDATA": temp}), \
             patch.object(provider.sys, "platform", "win32"), \
             patch.object(provider.sys, "path", list(provider.sys.path)):
            os.environ.pop("QMONEY_USER_ROOT", None)
            runpy.run_path(str(script), run_name="capture_entrypoint_fixture")
            self.assertNotIn("QMONEY_USER_ROOT", os.environ)
            self.assertEqual(provider.cache_path(), Path(temp) / "QMoney/data/appcheck_device.json")

    def test_live_obtain_does_not_authorize_from_disk_jwt(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.dict(os.environ, {"QMONEY_USER_ROOT": temp, "QMONEY_APPCHECK_SOURCE": "device"}), \
             patch.object(provider, "load_config", return_value={"package": "fixture.app"}), \
             patch.object(provider, "pick_device", return_value="fixture-serial"), \
             patch.object(provider, "collect_device_info", return_value={
                 "device_id": "android.ssaid:fixture", "serial": "fixture-serial", "model": "Fixture"}), \
             patch.object(provider, "ensure_appcheck", return_value={
                 "token": "private-fixture-canary", "provider": "fixture-provider",
                 "iat": 1, "exp": 9_999_999_999, "left": 1200}) as ensure, \
             patch.object(provider, "_jwt_left_s", return_value=1200):
            token = provider.obtain_token(force=True)
            self.assertEqual(token, "private-fixture-canary")
            ensure.assert_called_once_with("fixture-serial", {"package": "fixture.app"}, force=True)
            on_disk = json.loads((Path(temp) / "data/appcheck_device.json").read_text(encoding="utf-8"))
            self.assertNotIn("token", on_disk)
            self.assertEqual(on_disk["device_id"], "android.ssaid:fixture")
            status = provider.status_snapshot()
            self.assertEqual(status["device_id"], "android.ssaid:fixture")
            self.assertEqual(status["provider"], "fixture-provider")
            self.assertEqual(status["token_source"], "adb_live")
            self.assertTrue(status["token_available"])
            self.assertNotIn("private-fixture-canary", json.dumps(status))
            with patch.dict(os.environ, {"QMONEY_APPCHECK_SOURCE": "off", "QMONEY_APPCHECK_DEVICE": "0"}):
                status = provider.status_snapshot()
                self.assertFalse(status["enabled"])
                self.assertEqual(status["device_id"], "android.ssaid:fixture")

    def test_disk_jwt_alone_never_reports_validity_or_availability(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.dict(os.environ, {"QMONEY_USER_ROOT": temp, "QMONEY_APPCHECK_SOURCE": "device"}), \
             patch.object(provider, "load_config", return_value={"min_appcheck_left_s": 600}):
            path = provider._write_cache({
                "device_id": "android.ssaid:fixture",
                "serial": "fixture-serial",
                "token": "expired.jwt.should-not-surface",
                "token_source": "adb_live",
                "token_available": True,
                "provider": "play_integrity",
                "exp": 1,
                "left": -99999,
                "left_s": 999,
            })
            on_disk = json.loads(path.read_text(encoding="utf-8"))
            for key in ("token", "token_source", "token_available", "provider",
                        "exp", "left", "left_s"):
                self.assertNotIn(key, on_disk)
            status = provider.status_snapshot()
            self.assertFalse(status["token_available"])
            self.assertIsNone(status["left_s"])
            self.assertEqual(status["token_len"], 0)
            self.assertIsNone(status["provider"])
            self.assertIsNone(status["token_source"])
            self.assertEqual(status["device_id"], "android.ssaid:fixture")
            self.assertIsNone(provider.read_cached_token(min_left_s=1))

    def test_obtain_without_force_still_streams_from_device(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.dict(os.environ, {"QMONEY_USER_ROOT": temp, "QMONEY_APPCHECK_SOURCE": "device"}), \
             patch.object(provider, "load_config", return_value={"package": "fixture.app",
                                                                "min_appcheck_left_s": 600}), \
             patch.object(provider, "pick_device", return_value="fixture-serial"), \
             patch.object(provider, "collect_device_info", return_value={
                 "device_id": "android.ssaid:fixture", "serial": "fixture-serial"}), \
             patch.object(provider, "ensure_appcheck", return_value={
                 "token": "live-token", "provider": "play_integrity", "left": 1800}) as ensure, \
             patch.object(provider, "_jwt_left_s", return_value=1800):
            self.assertEqual(provider.obtain_token(force=False), "live-token")
            ensure.assert_called_once_with(
                "fixture-serial",
                {"package": "fixture.app", "min_appcheck_left_s": 600},
                force=False,
            )
            ensure.reset_mock()
            self.assertEqual(provider.obtain_token(force=False), "live-token")
            ensure.assert_not_called()
            ensure.reset_mock()
            self.assertEqual(
                provider.obtain_token(force=False, bypass_live_session=True),
                "live-token",
            )
            ensure.assert_called_once_with(
                "fixture-serial",
                {"package": "fixture.app", "min_appcheck_left_s": 600},
                force=False,
            )

    def test_explicit_profiles_do_not_read_each_others_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            first, second = Path(temp) / "one", Path(temp) / "two"
            with patch.dict(os.environ, {"QMONEY_USER_ROOT": str(first)}):
                provider._write_cache({"device_id": "android.ssaid:fixture"})
            with patch.dict(os.environ, {"QMONEY_USER_ROOT": str(second)}):
                self.assertIsNone(provider._read_cache_file())

    def test_local_edition_enables_device_mode_unless_explicitly_disabled(self):
        base = {
            "QMONEY_APPCHECK_SOURCE": "",
            "QMONEY_APPCHECK_DEVICE": "",
            "QMONEY_LOCAL_EDITION": "",
        }
        with patch.dict(os.environ, {**base, "QMONEY_LOCAL_EDITION": "1"}):
            self.assertTrue(provider.device_mode_enabled())
        with patch.dict(os.environ, base):
            self.assertFalse(provider.device_mode_enabled())
        with patch.dict(os.environ, {
                **base,
                "QMONEY_LOCAL_EDITION": "1",
                "QMONEY_APPCHECK_SOURCE": "off",
                "QMONEY_APPCHECK_DEVICE": "0"}):
            self.assertFalse(provider.device_mode_enabled())

    def test_write_defaults_activate_device_source_and_cached_serial(self):
        base = {
            "QMONEY_APPCHECK_SOURCE": "",
            "QMONEY_APPCHECK_DEVICE": "",
            "QMONEY_APPCHECK_ADB_SERIAL": "",
            "QMONEY_LOCAL_EDITION": "",
            "ANDROID_SERIAL": "",
        }
        with tempfile.TemporaryDirectory() as temp, \
             patch.dict(os.environ, {**base, "QMONEY_USER_ROOT": temp}, clear=False), \
             patch.object(provider, "_read_cache_file", return_value={"serial": "RQCT804KN2N"}):
            report = provider.ensure_device_defaults_for_writes()
            self.assertTrue(report["enabled"])
            self.assertTrue(report["activated_source"])
            self.assertTrue(report["activated_serial"])
            self.assertEqual(report["serial"], "RQCT804KN2N")
            self.assertEqual(os.environ.get("QMONEY_APPCHECK_SOURCE"), "device")
            self.assertEqual(os.environ.get("QMONEY_APPCHECK_ADB_SERIAL"), "RQCT804KN2N")

    def test_write_defaults_respect_explicit_disable(self):
        with patch.dict(os.environ, {
                "QMONEY_APPCHECK_SOURCE": "off",
                "QMONEY_APPCHECK_DEVICE": "0",
                "QMONEY_LOCAL_EDITION": "1",
                "QMONEY_APPCHECK_ADB_SERIAL": ""}, clear=False):
            report = provider.ensure_device_defaults_for_writes()
            self.assertTrue(report["explicitly_disabled"])
            self.assertFalse(report["enabled"])
            self.assertFalse(report["activated_source"])
            self.assertEqual(os.environ.get("QMONEY_APPCHECK_SOURCE"), "off")

    def test_write_defaults_prefer_android_serial_without_cache(self):
        base = {
            "QMONEY_APPCHECK_SOURCE": "device",
            "QMONEY_APPCHECK_DEVICE": "",
            "QMONEY_APPCHECK_ADB_SERIAL": "",
            "ANDROID_SERIAL": "RQCT804KN2N",
        }
        with patch.dict(os.environ, base, clear=False), \
             patch.object(provider, "_read_cache_file", return_value=None):
            report = provider.ensure_device_defaults_for_writes()
            self.assertTrue(report["enabled"])
            self.assertTrue(report["activated_serial"])
            self.assertEqual(report["serial"], "RQCT804KN2N")


class SoftEnsureAppCheckTests(unittest.TestCase):
    """Soft ADB read must not restart Minute; force owns refresh_appcheck."""

    def test_soft_returns_prefs_token_above_floor_without_restart(self):
        from moneymin.roglife_appcheck import capture

        cfg = {"min_appcheck_left_s": 600, "package": "fixture.app",
               "appcheck_xml": "/fixture.xml", "appcheck_token_key": "k"}
        live = {"token": "soft-jwt", "left": 1800, "provider": "play_integrity", "iat": 1}
        with patch.object(capture, "read_appcheck", return_value=live) as read, \
             patch.object(capture, "refresh_appcheck") as refresh:
            self.assertEqual(capture.ensure_appcheck("serial", cfg, force=False), live)
        read.assert_called_once()
        refresh.assert_not_called()

    def test_soft_missing_or_short_token_refuses_without_restart(self):
        from moneymin.roglife_appcheck import capture

        cfg = {"min_appcheck_left_s": 600, "package": "fixture.app",
               "appcheck_xml": "/fixture.xml", "appcheck_token_key": "k"}
        with patch.object(capture, "refresh_appcheck") as refresh:
            with patch.object(capture, "read_appcheck", return_value=None):
                with self.assertRaises(RuntimeError) as missing:
                    capture.ensure_appcheck("serial", cfg, force=False)
            self.assertIn("ausente", str(missing.exception).casefold())
            with patch.object(capture, "read_appcheck",
                              return_value={"token": "short", "left": 120, "iat": 1}):
                with self.assertRaises(RuntimeError) as short:
                    capture.ensure_appcheck("serial", cfg, force=False)
            self.assertIn("piso", str(short.exception).casefold())
        refresh.assert_not_called()

    def test_force_calls_refresh_when_prefs_unusable(self):
        from moneymin.roglife_appcheck import capture

        cfg = {"min_appcheck_left_s": 600, "package": "fixture.app",
               "appcheck_xml": "/fixture.xml", "appcheck_token_key": "k"}
        fresh = {"token": "forced-jwt", "left": 3500, "provider": "play_integrity", "iat": 9}
        with patch.object(capture, "read_appcheck", return_value=None), \
             patch.object(capture, "refresh_appcheck", return_value=fresh) as refresh:
            self.assertEqual(capture.ensure_appcheck("serial", cfg, force=True), fresh)
        refresh.assert_called_once_with("serial", cfg, baseline_iat=0)


class WakeDeviceRegressionTests(unittest.TestCase):
    """Dozing device must be woken around Minute restarts so the token is re-minted."""

    def test_restart_minute_wakes_screen_before_launch(self):
        from moneymin.roglife_appcheck import capture

        calls = []
        with patch.object(capture, "adb_shell",
                          side_effect=lambda serial, cmd, **kw: calls.append(cmd)), \
             patch.object(capture.time, "sleep"):
            capture.restart_minute("serial", "fixture.app")

        self.assertEqual(len(calls), 3)
        self.assertIn("KEYCODE_WAKEUP", calls[0])
        self.assertIn("dismiss-keyguard", calls[0])
        self.assertEqual(calls[1], "am force-stop fixture.app")
        self.assertIn("monkey -p fixture.app", calls[2])

    def test_refresh_polling_keeps_screen_awake_between_reads(self):
        from moneymin.roglife_appcheck import capture

        cfg = {"min_appcheck_left_s": 600, "package": "fixture.app",
               "appcheck_xml": "/fixture.xml", "appcheck_token_key": "k"}
        fresh = {"token": "fresh-jwt", "left": 1800, "provider": "play_integrity", "iat": 5}
        adb_calls = []
        reads = iter([None, fresh])
        clock = {"now": 0.0}

        with patch.object(capture, "adb_shell",
                          side_effect=lambda serial, cmd, **kw: adb_calls.append(cmd)), \
             patch.object(capture, "restart_minute"), \
             patch.object(capture, "read_appcheck", side_effect=lambda serial, cfg: next(reads)), \
             patch.object(capture.time, "time", side_effect=lambda: clock["now"]), \
             patch.object(capture.time, "sleep",
                          side_effect=lambda seconds: clock.__setitem__("now", clock["now"] + seconds)):
            token = capture.refresh_appcheck("serial", cfg, baseline_iat=0)

        self.assertEqual(token, fresh)
        self.assertTrue(any("KEYCODE_WAKEUP" in cmd for cmd in adb_calls))
