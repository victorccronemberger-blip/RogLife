"""The isolated test runner blocks real ADB launches without blocking mocks."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "scripts" / "run_tests.py"


def _runner_module():
    spec = importlib.util.spec_from_file_location("offline_test_runner", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load the isolated test runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_guarded_child(source: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="offline-adb-guard-test-") as folder:
        Path(folder, "sitecustomize.py").write_text(
            _runner_module().OFFLINE_GUARD, encoding="utf-8")
        environment = os.environ.copy()
        environment["PYTHONPATH"] = folder
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=folder,
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
        )


def _run_pytest_guard_child() -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="offline-adb-pytest-test-") as folder:
        root = Path(folder)
        guard = root / "guard"
        tests = root / "test_thread_violation.py"
        guard.mkdir()
        runner = _runner_module()
        (guard / "sitecustomize.py").write_text(
            runner.OFFLINE_GUARD, encoding="utf-8")
        (guard / "_offline_adb_pytest_plugin.py").write_text(
            runner.PYTEST_ADB_GUARD_PLUGIN, encoding="utf-8")
        tests.write_text(
            "import subprocess\n"
            "import threading\n"
            "def test_thread_catches_adb_guard_exception():\n"
            "    caught = []\n"
            "    def attempt():\n"
            "        try:\n"
            "            subprocess.Popen([r'C:\\\\offline_guard_sentinel\\\\adb.exe'])\n"
            "        except BaseException as exc:\n"
            "            caught.append(type(exc).__name__)\n"
            "    worker = threading.Thread(target=attempt)\n"
            "    worker.start()\n"
            "    worker.join(timeout=5)\n"
            "    assert not worker.is_alive()\n"
            "    assert caught == ['OfflineADBProcessError']\n",
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment.update(
            PYTHONPATH=str(guard),
            PYTEST_PLUGINS="_offline_adb_pytest_plugin",
            PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
            PYTHONNOUSERSITE="1",
        )
        return subprocess.run(
            [sys.executable, "-B", "-m", "pytest", "-q", str(tests)],
            cwd=root,
            env=environment,
            text=True,
            capture_output=True,
            timeout=15,
        )


class OfflineDeviceGuardTests(unittest.TestCase):
    def test_adb_executable_path_is_blocked_before_launch(self):
        result = _run_guarded_child(
            "import subprocess, sys\n"
            "try:\n"
            "    subprocess.Popen([r'C:\\offline_guard_sentinel\\adb.exe', 'devices'])\n"
            "except Exception:\n"
            "    sys.exit(2)\n"
            "except BaseException as exc:\n"
            "    assert type(exc).__name__ == 'OfflineADBProcessError'\n"
            "else:\n"
            "    sys.exit(3)\n")

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_adb_inside_shell_command_is_blocked_before_launch(self):
        result = _run_guarded_child(
            "import subprocess, sys\n"
            "try:\n"
            "    subprocess.Popen(r'echo harmless && C:\\offline_guard_sentinel\\adb.exe devices', shell=True)\n"
            "except Exception:\n"
            "    sys.exit(2)\n"
            "except BaseException as exc:\n"
            "    assert type(exc).__name__ == 'OfflineADBProcessError'\n"
            "else:\n"
            "    sys.exit(3)\n")

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_mocked_process_and_benign_real_process_are_allowed(self):
        result = _run_guarded_child(
            "import subprocess, sys; "
            "from unittest.mock import patch; "
            "mock = patch('subprocess.run', return_value='fixture').start(); "
            "assert subprocess.run(['adb.exe', 'devices']) == 'fixture'; "
            "mock.assert_called_once(); "
            "patch.stopall(); "
            "subprocess.run([sys.executable, '-c', 'pass'], check=True)")

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_adb_like_name_is_not_treated_as_adb(self):
        result = _run_guarded_child(
            "import subprocess, sys\n"
            "from unittest.mock import patch\n"
            "sys.audit('subprocess.Popen', None, ['C:\\\\offline_guard_sentinel\\\\notadb.exe'], None, None)\n"
            "with patch('subprocess.Popen', return_value='fixture') as popen:\n"
            "    assert subprocess.Popen(['notadb.exe']) == 'fixture'\n"
            "    popen.assert_called_once()\n")

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pytest_plugin_fails_when_thread_catches_guard_exception(self):
        result = _run_pytest_guard_child()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("1 passed", result.stdout)
        self.assertIn("blocked 1 real ADB process attempt", result.stderr)


if __name__ == "__main__":
    unittest.main()
