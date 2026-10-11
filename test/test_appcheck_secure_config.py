"""App Check configuration from the local vault; isolated and offline."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from moneymin import config, secure_store


FIELDS = {
    "FIREBASE_APP_CHECK_PROJECT_ID": "project_id",
    "FIREBASE_APP_CHECK_APP_ID": "app_id",
    "FIREBASE_APP_CHECK_API_KEY": "api_key",
    "FIREBASE_APP_CHECK_DEBUG_TOKEN": "debug_token",
}
FIXTURE = {
    "project_id": "fixture-project",
    "app_id": "1:123:android:fixture",
    "api_key": "fixture-key",
    "debug_token": "fixture-secret",
}


class AppCheckSecureConfigTests(unittest.TestCase):
    def _load(self, section=None, *, environment=None, dotenv=None):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            if dotenv:
                (root / ".env").write_text(dotenv, encoding="utf-8")
            env = {"QMONEY_USER_ROOT": str(root), **(environment or {})}
            settings = {} if section is None else {"appcheck": section}
            with patch.dict(os.environ, env, clear=True), patch.object(
                secure_store, "load_secure_settings", return_value=settings
            ) as load:
                spec = importlib.util.spec_from_file_location(
                    "moneymin._appcheck_config_fixture", config.__file__
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                load.assert_called_once_with(root / "secrets" / "integrations.dat")
                return {key: os.environ.get(key) for key in FIELDS}

    def test_complete_vault_configuration_loads_without_dotenv(self):
        values = self._load(FIXTURE)
        self.assertEqual(values, {key: FIXTURE[field] for key, field in FIELDS.items()})

    def test_explicit_process_values_including_empty_override_vault(self):
        for key in FIELDS:
            for value in ("explicit-value", ""):
                with self.subTest(key=key, empty=not value):
                    values = self._load(FIXTURE, environment={key: value})
                    expected = {name: FIXTURE[field] for name, field in FIELDS.items()}
                    expected[key] = value
                    self.assertEqual(values, expected)

    def test_complete_vault_overrides_legacy_dotenv(self):
        dotenv = "\n".join(f"{key}=legacy-{field}" for key, field in FIELDS.items())
        values = self._load(FIXTURE, dotenv=dotenv)
        self.assertEqual(values, {key: FIXTURE[field] for key, field in FIELDS.items()})

    def test_absent_invalid_or_partial_section_does_not_inject_settings(self):
        candidates = [None, "invalid", [], {**FIXTURE, "debug_token": 123}]
        for field in FIXTURE:
            candidates.append({key: value for key, value in FIXTURE.items() if key != field})
            candidates.append({**FIXTURE, field: "  "})
        for section in candidates:
            with self.subTest(section=section):
                self.assertEqual(self._load(section), dict.fromkeys(FIELDS))

    def test_partial_vault_preserves_complete_legacy_configuration(self):
        dotenv = "\n".join(f"{key}=legacy-{field}" for key, field in FIELDS.items())
        values = self._load({"debug_token": "incomplete"}, dotenv=dotenv)
        self.assertEqual(values, {key: f"legacy-{field}" for key, field in FIELDS.items()})

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI required")
    def test_real_dpapi_update_preserves_other_sections_and_has_no_plaintext_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "integrations.dat"
            existing = {"schema": 1, "hostinger": {"token": "other-fixture"},
                        "ego4d": {"access_key_id": "fixture-access"}}
            secure_store.save_secure_settings(path, existing)
            secure_store.update_secure_section(path, "appcheck", FIXTURE)
            loaded = secure_store.load_secure_settings(path, strict=True)
            self.assertEqual(loaded, {**existing, "appcheck": FIXTURE})
            self.assertNotIn(FIXTURE["debug_token"].encode(), path.read_bytes())
            self.assertEqual(list(Path(folder).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
