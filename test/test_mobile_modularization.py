"""Compatibility and ownership contracts for the mobile subsystem; fixtures only."""
from __future__ import annotations

import ast
import base64
from contextlib import nullcontext
import inspect
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import typing
import unittest
from unittest.mock import MagicMock, patch

from moneymin import appcheck
from moneymin.mobile.appcheck import contracts


class MobileModularizationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        data = patch.object(appcheck.config, "DATA_DIR", Path(folder.name))
        data.start()
        self.addCleanup(data.stop)
        appcheck.clear_cache()
        self.addCleanup(appcheck.clear_cache)

    def test_runtime_and_facade_share_the_single_historical_state(self):
        appcheck._token = "fixture"
        appcheck._expires = 123.0
        self.assertEqual(appcheck._runtime.state.token, "fixture")
        self.assertEqual(appcheck._runtime.state.expires, 123.0)
        self.assertIs(appcheck._runtime.state.lock, appcheck._lock)
        appcheck._runtime.state.retry_at = 456.0
        self.assertEqual(appcheck._retry_at, 456.0)
        appcheck.clear_cache()
        self.assertIsNone(appcheck._runtime.state.token)
        self.assertEqual(appcheck._retry_at, 0.0)

    def test_runtime_resolves_hooks_replaced_after_import(self):
        with patch.object(appcheck, "mint_app_check_token", return_value="fixture") as mint:
            self.assertEqual(appcheck.get_app_check_header(), {"X-Firebase-AppCheck": "fixture"})
        mint.assert_called_once_with()
        expected = {"paused": True}
        with patch.object(appcheck, "app_check_status", return_value=expected) as status:
            self.assertIs(appcheck.ensure_app_check_fresh(), expected)
        status.assert_called_once_with()

    def test_lock_replaced_after_runtime_construction_is_observed(self):
        replacement = MagicMock()
        with patch.object(appcheck, "_lock", replacement):
            self.assertIs(appcheck._runtime.state.lock, replacement)
            appcheck.last_mint_failure()
        replacement.__enter__.assert_called_once_with()
        replacement.__exit__.assert_called_once()

    def test_device_boundary_is_lazy_replaceable_and_outside_state_lock(self):
        payload = base64.urlsafe_b64encode(json.dumps({"exp": 5000}).encode()).decode().rstrip("=")
        token = f"fixture.{payload}.signature"

        def obtain_token(**kwargs):
            acquired = []

            def probe():
                ok = appcheck._lock.acquire(timeout=0.5)
                acquired.append(ok)
                if ok:
                    appcheck._lock.release()

            thread = threading.Thread(target=probe)
            thread.start()
            thread.join(timeout=1)
            self.assertEqual(acquired, [True], "device operation held the shared cache lock")
            self.assertEqual(kwargs, {"force": False, "bypass_live_session": False})
            return token

        provider = SimpleNamespace(device_mode_enabled=lambda: True, obtain_token=obtain_token)
        with patch.object(appcheck, "legacy_provider", return_value=provider), \
                patch.object(appcheck.time, "time", return_value=1000):
            self.assertEqual(appcheck.mint_app_check_token(), token)
            self.assertEqual(appcheck._token, token)

    def test_configuration_and_storage_follow_current_facade_paths(self):
        other = Path("fixture-other-profile")
        with patch.object(appcheck.config, "DATA_DIR", other):
            self.assertEqual(appcheck._state_path(), other / "appcheck_mint_state.json")
            self.assertEqual(appcheck._lock_path(), other / "appcheck_exchange.lock")

    def test_exchange_file_lock_resolves_path_when_entered(self):
        path = Path("fixture-profile") / "appcheck_exchange.lock"
        with patch.object(appcheck, "_lock_path", return_value=path) as resolve, \
                patch.object(appcheck.persistence, "exchange_file_lock", return_value=nullcontext()) as lock:
            deferred = appcheck._exchange_file_lock()
            resolve.assert_not_called()
            lock.assert_not_called()
            with deferred:
                resolve.assert_called_once_with()
                lock.assert_called_once_with(path)

    def test_jwt_inspection_uses_current_clock_without_claiming_signature_validation(self):
        payload = base64.urlsafe_b64encode(b'{"exp":1000}').decode().rstrip("=")
        token = f"header.{payload}.unverified"
        with patch.object(appcheck.time, "time", return_value=900):
            self.assertTrue(appcheck._bearer_still_valid(token))
        with patch.object(appcheck.time, "time", return_value=980):
            self.assertFalse(appcheck._bearer_still_valid(token))

    def test_mobile_domain_does_not_import_compatibility_facade_or_http_composition(self):
        root = Path(__file__).resolve().parents[1] / "moneymin" / "mobile"
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            with self.subTest(path=path.relative_to(root)):
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        self.assertNotEqual(node.module, "moneymin.appcheck")
                        self.assertNotEqual(node.module, "moneymin.web.server")
                        self.assertFalse(any(alias.name == "appcheck" for alias in node.names))
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                        self.assertNotIn(node.func.id, {"globals", "exec", "eval"})

    def test_public_keyword_contracts_are_preserved(self):
        mint = inspect.signature(appcheck.mint_app_check_token).parameters
        self.assertEqual(list(mint), ["force_refresh", "bypass_live_session"])
        self.assertFalse(mint["force_refresh"].default)
        self.assertEqual(mint["bypass_live_session"].kind, inspect.Parameter.KEYWORD_ONLY)
        fresh = inspect.signature(appcheck.ensure_app_check_fresh).parameters
        self.assertEqual(fresh["min_left_s"].kind, inspect.Parameter.KEYWORD_ONLY)
        self.assertIsNone(fresh["min_left_s"].default)
        # Protocol annotations remain usable by maintenance/type tooling.
        self.assertEqual(typing.get_type_hints(contracts.AppCheckPorts._state_path)["return"], Path)

    def test_mobile_ui_methods_have_one_owner_in_the_shared_build(self):
        root = Path(__file__).resolve().parents[1]
        mobile = (root / "desktop/src/MainWindowMobile.cpp").read_text(encoding="utf-8")
        integrations = (root / "desktop/src/MainWindowIntegrations.cpp").read_text(encoding="utf-8")
        for name in ("renderAppCheckSnapshot", "recheckAppCheck"):
            marker = f"void MainWindow::{name}("
            self.assertEqual(mobile.count(marker), 1)
            self.assertNotIn(marker, integrations)
        cmake = (root / "desktop/CMakeLists.txt").read_text(encoding="utf-8")
        start = cmake.index("set(QMONEY_MAIN_WINDOW_SOURCES")
        self.assertIn("src/MainWindowMobile.cpp", cmake[start:cmake.index(")", start)])


if __name__ == "__main__":
    unittest.main()
