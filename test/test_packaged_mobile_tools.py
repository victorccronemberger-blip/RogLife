from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from moneymin.roglife_appcheck import config


ROOT = Path(__file__).resolve().parents[1]
_SERVICE_SPEC = importlib.util.spec_from_file_location(
    "qmoney_service_mobile_fixture", ROOT / "packaging" / "qmoney_service.py")
assert _SERVICE_SPEC and _SERVICE_SPEC.loader
service = importlib.util.module_from_spec(_SERVICE_SPEC)
_SERVICE_SPEC.loader.exec_module(service)


class RogLifeConfigDistributionTests(unittest.TestCase):
    @staticmethod
    def _config(package: str) -> dict[str, str]:
        return {
            "package": package,
            "appcheck_xml": "fixture-public-app-id",
            "appcheck_token_key": "fixture-public-token-key",
        }

    def test_explicit_override_precedes_vendor_and_bundled_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            override = root / "operator.json"
            vendor = root / "vendor.json"
            bundled = root / "bundled.json"
            override.write_text(json.dumps(self._config("operator.package")), encoding="utf-8")
            vendor.write_text(json.dumps(self._config("vendor.package")), encoding="utf-8")
            bundled.write_text(json.dumps(self._config("bundled.package")), encoding="utf-8")
            with patch.dict(os.environ, {"QMONEY_ROGLIFE_CONFIG": str(override)}), \
                 patch.object(config, "_DEFAULT_CONFIG", vendor), \
                 patch.object(config, "_BUNDLED_DEFAULT_CONFIG", bundled):
                self.assertEqual(config.config_path(), override.resolve())
                self.assertEqual(config.load_config()["package"], "operator.package")

    def test_existing_vendor_config_precedes_bundled_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vendor = root / "vendor.json"
            bundled = root / "bundled.json"
            vendor.write_text(json.dumps(self._config("vendor.package")), encoding="utf-8")
            bundled.write_text(json.dumps(self._config("bundled.package")), encoding="utf-8")
            with patch.dict(os.environ, {"QMONEY_ROGLIFE_CONFIG": ""}), \
                 patch.object(config, "_DEFAULT_CONFIG", vendor), \
                 patch.object(config, "_BUNDLED_DEFAULT_CONFIG", bundled):
                self.assertEqual(config.config_path(), vendor)
                self.assertEqual(config.load_config()["package"], "vendor.package")

    def test_bundled_fallback_contains_only_allowlisted_fields(self):
        allowed = {
            "package", "appcheck_xml", "appcheck_token_key", "min_appcheck_left_s",
            "prefer_physical_device", "exclude_adb_serials",
        }
        with patch.dict(os.environ, {"QMONEY_ROGLIFE_CONFIG": ""}), \
             patch.object(config, "_DEFAULT_CONFIG", ROOT / "vendor" / "missing-config.json"):
            self.assertEqual(config.config_path(), config._BUNDLED_DEFAULT_CONFIG)
            loaded = config.load_config()
        self.assertEqual(set(loaded), allowed)
        self.assertEqual(loaded["exclude_adb_serials"], [])
        self.assertGreater(loaded["min_appcheck_left_s"], 0)
        self.assertTrue(loaded["prefer_physical_device"])


class PackagedAdbBootstrapTests(unittest.TestCase):
    def test_private_adb_is_added_to_path_and_configured_when_present(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tools = root / "tools" / "platform-tools"
            tools.mkdir(parents=True)
            adb = tools / "adb.exe"
            adb.write_bytes(b"fixture binary; never executed")
            environment = {"PATH": "existing-path"}

            result = service.configure_packaged_mobile_tools(
                runtime_root=root, environ=environment)

            self.assertEqual(result, adb)
            self.assertEqual(environment["PATH"].split(os.pathsep)[0], str(tools))
            self.assertEqual(environment["QMONEY_ADB_PATH"], str(adb))

    def test_explicit_adb_override_keeps_priority_and_private_path_available(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tools = root / "tools" / "platform-tools"
            tools.mkdir(parents=True)
            (tools / "adb.exe").write_bytes(b"fixture binary; never executed")
            custom_tools = root / "operator" / "platform-tools"
            custom_tools.mkdir(parents=True)
            custom_adb = custom_tools / "adb.exe"
            custom_adb.write_bytes(b"custom fixture binary; never executed")
            environment = {
                "PATH": os.pathsep.join((str(tools), "existing-path")),
                "QMONEY_ADB_PATH": str(custom_adb),
            }

            result = service.configure_packaged_mobile_tools(
                runtime_root=root, environ=environment)

            self.assertEqual(result, tools / "adb.exe")
            self.assertEqual(environment["QMONEY_ADB_PATH"], str(custom_adb))
            self.assertEqual(environment["PATH"].split(os.pathsep)[:2], [str(custom_tools), str(tools)])
            self.assertEqual(environment["PATH"].split(os.pathsep).count(str(tools)), 1)

    def test_missing_private_adb_does_not_change_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment = {"PATH": "existing-path", "QMONEY_ADB_PATH": "custom-adb.exe"}
            before = dict(environment)

            result = service.configure_packaged_mobile_tools(
                runtime_root=Path(temporary), environ=environment)

            self.assertIsNone(result)
            self.assertEqual(environment, before)


if __name__ == "__main__":
    unittest.main()
