"""Isolated public ADB diagnostics: no executable or phone is contacted."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from moneymin.integrations import android_diagnostics as diagnostics


_ADB = "C:/Android/platform-tools/adb.exe"
_CONNECTED = "List of devices attached\nPHONE123 device product:pixel model:Pixel_7 device:panther transport_id:1\n"
_PROPS = {
    "ro.product.model": "Pixel 7\n",
    "ro.product.manufacturer": "Google\n",
    "ro.build.version.release": "14\n",
    "ro.build.version.sdk": "34\n",
}


def _fake_run(inventory=_CONNECTED, *, uid="uid=2000(shell) gid=2000(shell)\n", su="", su_code=1):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        assert isinstance(args, list)
        assert args[0] == _ADB
        assert kwargs["shell"] is False
        assert 0 < kwargs["timeout"] <= 8
        if args[1:] == ["devices", "-l"]:
            return subprocess.CompletedProcess(args, 0, inventory, "")
        assert args[1:4] == ["-s", "PHONE123", "shell"]
        tail = args[4:]
        if len(tail) == 2 and tail[0] == "getprop":
            assert tail[1] in _PROPS
            return subprocess.CompletedProcess(args, 0, _PROPS[tail[1]], "")
        if tail == ["id"]:
            return subprocess.CompletedProcess(args, 0, uid, "")
        assert tail == ["which", "su"]
        return subprocess.CompletedProcess(args, su_code, su, "")

    return run, calls


def _assert_public_commands(calls):
    for args, _ in calls:
        if args[1:] == ["devices", "-l"]:
            continue
        assert args[1:4] == ["-s", "PHONE123", "shell"]
        tail = args[4:]
        assert tail in (["id"], ["which", "su"]) or (
            len(tail) == 2 and tail[0] == "getprop" and tail[1] in _PROPS
        )
        assert tail[0] != "su"
        assert not any("/data/data" in argument for argument in args)


def _snapshot(run, serial=None):
    with patch.object(diagnostics, "_find_adb", return_value=_ADB), patch.object(
        diagnostics.subprocess, "run", side_effect=run
    ):
        return diagnostics.snapshot(serial)


def _assert_empty(result):
    assert result["device"] == {"model": "", "manufacturer": "", "android_version": "", "sdk": ""}
    assert result["root"]["state"] == "unknown"
    assert result["collection"]["elapsed_ms"] >= 0
    assert datetime.fromisoformat(result["collection"]["checked_at"].replace("Z", "+00:00")).utcoffset().total_seconds() == 0
    assert set(result) == {"adb_available", "connection_state", "devices", "selected_serial", "device", "root", "collection"}


def test_missing_adb_has_stable_unavailable_fields():
    with patch.object(diagnostics, "_find_adb", return_value=None), patch.object(diagnostics.subprocess, "run") as run:
        result = diagnostics.snapshot()
    run.assert_not_called()
    assert result["adb_available"] is False
    assert result["connection_state"] == "adb_missing"
    assert result["collection"]["state"] == "unavailable"
    assert "Platform-Tools" in result["collection"]["message"]
    assert result["devices"] == []
    assert result["selected_serial"] is None
    _assert_empty(result)


@pytest.mark.parametrize("line,state", [
    ("", "disconnected"),
    ("PHONE123 unauthorized\n", "unauthorized"),
    ("PHONE123 offline\n", "offline"),
])
def test_unavailable_device_does_not_run_shell(line, state):
    run, calls = _fake_run("List of devices attached\n" + line)
    result = _snapshot(run)
    assert result["adb_available"] is True
    assert result["connection_state"] == state
    assert result["collection"]["state"] == "unavailable"
    assert len(calls) == 1
    _assert_empty(result)


def test_multiple_authorized_devices_require_selection():
    run, calls = _fake_run(_CONNECTED + "PHONE456 device model:Pixel_8\n")
    result = _snapshot(run)
    assert result["connection_state"] == "multiple"
    assert result["selected_serial"] is None
    assert len(result["devices"]) == 2
    assert len(calls) == 1
    _assert_empty(result)


def test_multiple_unavailable_devices_require_selection():
    run, calls = _fake_run("PHONE123 unauthorized\nPHONE456 offline\n")
    result = _snapshot(run)
    assert result["connection_state"] == "multiple"
    assert len(calls) == 1


def test_unique_authorized_device_is_selected_among_unavailable_devices():
    run, calls = _fake_run(_CONNECTED + "PHONE456 unauthorized\n")
    result = _snapshot(run)
    assert result["connection_state"] == "connected"
    assert result["selected_serial"] == "PHONE123"
    _assert_public_commands(calls)


def test_explicit_selection_queries_only_inventory_member():
    run, calls = _fake_run(_CONNECTED + "PHONE456 device model:Pixel_8\n")
    result = _snapshot(run, "PHONE123")
    assert result["connection_state"] == "connected"
    assert result["selected_serial"] == "PHONE123"
    _assert_public_commands(calls)


def test_explicit_unauthorized_selection_preserves_authorization_state():
    run, calls = _fake_run(_CONNECTED + "PHONE456 unauthorized\n")
    result = _snapshot(run, "PHONE456")
    assert result["connection_state"] == "unauthorized"
    assert result["selected_serial"] == "PHONE456"
    assert len(calls) == 1


@pytest.mark.parametrize("serial", ["PHONE123; su -c id", "-s PHONE123", "PHONE123\nreboot", "", 12])
def test_malformed_or_injected_serial_never_runs_shell(serial):
    run, calls = _fake_run()
    result = _snapshot(run, serial)
    assert result["connection_state"] == "error"
    assert result["collection"]["state"] == "error"
    assert result["selected_serial"] is None
    assert len(calls) == 1
    _assert_empty(result)


@pytest.mark.parametrize("inventory,expected_devices", [
    ("List of devices attached\n", []),
    ("List of devices attached\nlocalhost:6520 device model:Test_Emulator\n",
     [{"serial": "localhost:6520", "state": "device", "model": "Test Emulator"}]),
])
def test_missing_selected_device_is_disconnected_without_switching_to_emulator(inventory, expected_devices):
    run, calls = _fake_run(inventory)
    result = _snapshot(run, "PHONE123")
    assert result["adb_available"] is True
    assert result["connection_state"] == "disconnected"
    assert result["collection"]["state"] == "unavailable"
    assert "selecionado" in result["collection"]["message"]
    assert result["selected_serial"] == "PHONE123"
    assert result["devices"] == expected_devices
    assert len(calls) == 1
    _assert_empty(result)


def test_malicious_inventory_serial_is_not_used_as_selection():
    run, calls = _fake_run("PHONE123;reboot device model:Bad\n")
    result = _snapshot(run)
    assert result["connection_state"] == "error"
    assert len(calls) == 1
    _assert_empty(result)


def test_valid_public_device_information_and_absent_root():
    run, calls = _fake_run()
    result = _snapshot(run)
    assert result["devices"] == [{"serial": "PHONE123", "state": "device", "model": "Pixel 7"}]
    assert result["device"] == {"model": "Pixel 7", "manufacturer": "Google", "android_version": "14", "sdk": "34"}
    assert result["connection_state"] == "connected"
    assert result["collection"]["state"] == "done"
    assert result["root"]["state"] == "not_detected"
    _assert_public_commands(calls)


@pytest.mark.parametrize("uid,su,su_code,state", [
    ("uid=0(root) gid=0(root)", "", 1, "root_shell"),
    ("uid=2000(shell) gid=2000(shell)", "/system/xbin/su\n", 0, "su_available"),
    ("uid=1000(system)", "", 1, "not_detected"),
    ("identity unavailable", "", 1, "unknown"),
])
def test_root_detection_does_not_execute_su_or_assert_app_permission(uid, su, su_code, state):
    run, calls = _fake_run(uid=uid, su=su, su_code=su_code)
    result = _snapshot(run)
    assert result["root"]["state"] == state
    assert "aplicativo" in result["root"]["message"]
    assert result["collection"]["state"] == "done"
    _assert_public_commands(calls)


def test_timeout_is_actionable_and_does_not_return_raw_output():
    run, calls = _fake_run()

    def timeout(args, **kwargs):
        if args[1:] == ["devices", "-l"]:
            return run(args, **kwargs)
        calls.append((args, kwargs))
        raise subprocess.TimeoutExpired(args, kwargs["timeout"], output="private-token", stderr="raw-secret")

    result = _snapshot(timeout)
    assert result["connection_state"] == "error"
    assert result["collection"]["state"] == "error"
    assert "Tempo limite" in result["collection"]["message"]
    assert "private-token" not in str(result)
    assert "raw-secret" not in str(result)
    assert len(calls) == 2
    _assert_empty(result)
    _assert_public_commands(calls)


@pytest.mark.parametrize("stderr,state", [
    ("error: device 'PHONE123' not found; sensitive-token", "disconnected"),
    ("error: no devices/emulators found", "disconnected"),
    ("error: device offline", "offline"),
    ("error: device unauthorized. secret", "unauthorized"),
    ("error: transport failed; password=secret", "error"),
])
def test_hot_unplug_and_transport_errors_are_preserved_without_stderr(stderr, state):
    run, calls = _fake_run()

    def unplug(args, **kwargs):
        if args[1:] == ["devices", "-l"]:
            return run(args, **kwargs)
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 1, "", stderr)

    result = _snapshot(unplug)
    assert result["connection_state"] == state
    assert result["collection"]["state"] == "error"
    assert stderr not in str(result)
    assert "secret" not in str(result)
    assert len(calls) == 2
    _assert_empty(result)
    _assert_public_commands(calls)


def test_global_deadline_is_shared_and_never_restarts_for_each_command():
    run, calls = _fake_run()
    with patch.object(diagnostics.time, "monotonic", side_effect=[0.0, 1.0, 3.0, 8.0, 8.0]):
        result = _snapshot(run)
    assert [kwargs["timeout"] for _, kwargs in calls] == [7.0, 5.0]
    assert result["connection_state"] == "error"
    assert result["collection"]["elapsed_ms"] == 8000
    assert "Tempo limite" in result["collection"]["message"]


def test_windows_subprocess_is_hidden_and_has_no_shell():
    run, calls = _fake_run()
    with patch.object(diagnostics, "_WINDOWS", True):
        result = _snapshot(run)
    assert result["connection_state"] == "connected"
    assert all(kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) for _, kwargs in calls)
    assert all(kwargs["shell"] is False for _, kwargs in calls)


def test_missing_executable_after_resolution_reports_adb_missing():
    result = _snapshot(lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("private path")))
    assert result["adb_available"] is False
    assert result["connection_state"] == "adb_missing"
    assert "private path" not in str(result)
    _assert_empty(result)


def test_adb_resolution_checks_explicit_path_first(tmp_path, monkeypatch):
    executable = tmp_path / "custom-adb.exe"
    executable.write_bytes(b"")
    monkeypatch.setenv("QMONEY_ADB_PATH", str(executable))
    with patch.object(diagnostics.shutil, "which") as which:
        assert diagnostics._find_adb() == str(executable)
    which.assert_not_called()


def test_adb_resolution_uses_path_before_sdk_roots(monkeypatch):
    monkeypatch.delenv("QMONEY_ADB_PATH", raising=False)
    with patch.object(diagnostics.shutil, "which", return_value=_ADB):
        assert diagnostics._find_adb() == _ADB


def test_adb_resolution_uses_standard_sdk_root(tmp_path, monkeypatch):
    platform_tools = tmp_path / "platform-tools"
    platform_tools.mkdir()
    executable = platform_tools / ("adb.exe" if diagnostics._WINDOWS else "adb")
    executable.write_bytes(b"")
    monkeypatch.delenv("QMONEY_ADB_PATH", raising=False)
    monkeypatch.setenv("ANDROID_SDK_ROOT", str(tmp_path))
    with patch.object(diagnostics.shutil, "which", return_value=None):
        assert diagnostics._find_adb() == str(executable)


def test_adb_resolution_does_not_walk_or_read_configurations(tmp_path, monkeypatch):
    for variable in ("QMONEY_ADB_PATH", "ANDROID_SDK_ROOT", "ANDROID_HOME", "LOCALAPPDATA"):
        monkeypatch.delenv(variable, raising=False)
    with patch.object(diagnostics.shutil, "which", return_value=None), patch.object(
        diagnostics.Path, "home", return_value=tmp_path
    ), patch.object(Path, "read_text", side_effect=AssertionError("must not read settings")), patch.object(
        Path, "rglob", side_effect=AssertionError("must not scan directories")
    ):
        assert diagnostics._find_adb() is None
