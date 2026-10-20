"""Exercise the launcher in a temporary distribution; never start the real app."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="Windows PowerShell required")
ROOT = Path(__file__).resolve().parents[1]


def launch_fixture(tmp_path, *, installed=True, built=None, pending=None, active=False):
    root = tmp_path / "local copy with spaces"
    (root / "scripts").mkdir(parents=True)
    (root / "dist-local").mkdir()
    (root / "build-modular2").mkdir()
    shutil.copyfile(ROOT / "scripts/start_local.ps1", root / "scripts/start_local.ps1")

    def binary(relative, value, timestamp):
        path = root / relative
        path.write_text(value)
        os.utime(path, (timestamp, timestamp))

    if installed:
        binary("dist-local/QMoney.exe", "installed", 200)
    if built:
        binary("build-modular2/QMoney.exe", *built)
    if pending:
        binary("dist-local/QMoney.next.exe", *pending)
    wrapper = root / "run.ps1"
    wrapper.write_text(r'''
$ErrorActionPreference = 'Stop'
function Get-Process {
    param($ErrorAction)
    if ($env:LOCAL_LAUNCH_TEST_ACTIVE -eq '1') {
        [pscustomobject]@{ Path = (Join-Path $PSScriptRoot 'dist-local\QMoney.exe') }
    }
}
function Start-Process {
    param($FilePath, $WorkingDirectory, $WindowStyle)
    if ($FilePath -ne (Join-Path $PSScriptRoot 'dist-local\QMoney.exe')) {
        throw 'Unexpected executable'
    }
    if ($WorkingDirectory -ne (Join-Path $PSScriptRoot 'dist-local')) {
        throw 'Unexpected working directory'
    }
    Set-Content -LiteralPath (Join-Path $PSScriptRoot 'launched') -Value 'mock'
}
& "$PSScriptRoot\scripts\start_local.ps1"
''', encoding="utf-8")
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() != "PSMODULEPATH"}
    environment["LOCAL_LAUNCH_TEST_ACTIVE"] = "1" if active else "0"
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)],
        env=environment,
        capture_output=True, timeout=30,
    )
    return root, result


@pytest.mark.parametrize("built,pending,expected", [
    (("compiled", 300), None, "compiled"),
    (("older", 100), None, "installed"),
    (None, None, "installed"),
    (("compiled", 300), ("staged", 400), "staged"),
    (("compiled", 400), ("staged", 300), "compiled"),
    (None, ("staged", 300), "staged"),
    (("installed", 300), None, "installed"),
])
def test_launcher_selects_latest_local_binary(tmp_path, built, pending, expected):
    root, result = launch_fixture(tmp_path, built=built, pending=pending)
    assert result.returncode == 0, result.stderr
    assert (root / "dist-local/QMoney.exe").read_text() == expected
    assert (root / "launched").exists()
    assert not (root / "dist-local/QMoney.next.exe").exists()
    backup = root / "dist-local/QMoney.previous.exe"
    if expected != "installed":
        assert backup.read_text() == "installed"
    else:
        assert not backup.exists()


def test_launcher_preserves_active_instance_and_pending_update(tmp_path):
    root, result = launch_fixture(
        tmp_path, built=("compiled", 400), pending=("staged", 300), active=True,
    )
    assert result.returncode == 0, result.stderr
    assert (root / "dist-local/QMoney.exe").read_text() == "installed"
    assert (root / "dist-local/QMoney.next.exe").read_text() == "staged"
    assert not (root / "launched").exists()
    assert not (root / "dist-local/QMoney.previous.exe").exists()


def test_launcher_does_not_start_incomplete_distribution(tmp_path):
    root, result = launch_fixture(tmp_path, installed=False, built=("compiled", 300))
    assert result.returncode != 0
    assert not (root / "launched").exists()
