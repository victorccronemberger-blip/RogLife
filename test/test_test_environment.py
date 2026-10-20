"""Process configuration selected by a failing test must not reach its successor."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


class TestEnvironmentIsolationTests(unittest.TestCase):
    def test_provider_selection_is_restored_even_after_failure(self):
        with tempfile.TemporaryDirectory(prefix="test-environment-isolation-") as folder:
            root = Path(folder)
            shutil.copyfile(Path(__file__).with_name("conftest.py"), root / "conftest.py")
            keys = ("QMONEY_APPCHECK_SOURCE", "QMONEY_APPCHECK_DEVICE", "ANDROID_SERIAL")
            expected = {key: os.environ.get(key) for key in keys}
            (root / "test_environment_fixture.py").write_text(
                "import os\n"
                "def test_01_selects_device_and_fails():\n"
                "    os.environ['QMONEY_APPCHECK_SOURCE'] = 'device'\n"
                "    os.environ['QMONEY_APPCHECK_DEVICE'] = '1'\n"
                "    os.environ['ANDROID_SERIAL'] = 'UNIT-TEST-ONLY'\n"
                "    raise AssertionError('intentional fixture failure')\n"
                "def test_02_observes_original_configuration():\n"
                f"    assert {{key: os.environ.get(key) for key in {keys!r}}} == {expected!r}\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, "-B", "-m", "pytest", "-q", str(root)],
                cwd=root, env=os.environ.copy(), capture_output=True, text=True,
                timeout=30, check=False,
            )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("1 failed, 1 passed", result.stdout)
        self.assertIn("intentional fixture failure", result.stdout)
