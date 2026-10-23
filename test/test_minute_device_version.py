"""Installed-version contracts, using synthetic devices and substituted ADB only."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import inspect
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from moneymin import config
from moneymin.mobile import minute_version as versions
from moneymin.roglife_appcheck import adb, config as roglife_config, provider

PACKAGE = "com.bakerdata.minute"
SERIAL_A = "fixture-usb-a"
SERIAL_B = "fixture-usb-b"
UPDATED = "2026-01-02 03:04:05"


def package_dump(name="1.30.0", code="1004039", *, package=PACKAGE, updated=UPDATED):
    optional = "" if updated is None else f"    lastUpdateTime={updated}\n"
    return (
        "Packages:\n"
        f"  Package [{package}] (fixture):\n"
        f"    versionCode={code} minSdk=24 targetSdk=36\n"
        f"    versionName={name}\n"
        f"{optional}"
        "    User 0: installed=true hidden=false stopped=false\n"
        "Queries:\n"
    )


def completed(stdout=None, returncode=0):
    return subprocess.CompletedProcess(
        args=["fixture-only"], returncode=returncode,
        stdout=package_dump() if stdout is None else stdout,
        stderr="fixture-private-transport-detail",
    )


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """Every test starts/ends with empty memory and no path to a real command."""
    versions.clear_observation_cache()
    cfg = {"package": PACKAGE, "prefer_physical_device": True, "exclude_adb_serials": []}
    clock = SimpleNamespace(now=100.0)
    listing = Mock(return_value=[{"serial": SERIAL_A}])
    shell = Mock(return_value=completed())

    def forbidden(*args, **kwargs):
        pytest.fail("Real commands, private device collection and disk authority are forbidden")

    monkeypatch.setenv("QMONEY_USER_ROOT", "fixture-profile-a")
    monkeypatch.setenv("LOCALAPPDATA", "fixture-local")
    monkeypatch.delenv("QMONEY_APPCHECK_ADB_SERIAL", raising=False)
    monkeypatch.delenv("ANDROID_SERIAL", raising=False)
    monkeypatch.delenv("QMONEY_ROGLIFE_CONFIG", raising=False)
    monkeypatch.setattr(config, "ROOT", Path("fixture-profile-a"))
    monkeypatch.setattr(config, "DATA_DIR", Path("fixture-profile-a/data"))
    monkeypatch.setattr(roglife_config, "config_path", lambda: Path("fixture-public-config.json"))
    load = Mock(side_effect=lambda: dict(cfg))
    monkeypatch.setattr(roglife_config, "load_config", load)
    monkeypatch.setattr(adb, "list_adb_devices", listing)
    monkeypatch.setattr(adb, "adb_shell", shell)
    monkeypatch.setattr(adb, "adb", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(provider, "device_mode_enabled", lambda: True)
    for name in ("_read_cache_file", "_write_cache", "physical_identity", "physical_app_version",
                 "ensure_device_defaults_for_writes", "obtain_token", "ensure_appcheck", "collect_device_info"):
        monkeypatch.setattr(provider, name, forbidden)
    # Replace this module's clock, not the global clock used by thread timeouts.
    monkeypatch.setattr(versions, "time", SimpleNamespace(monotonic=lambda: clock.now))
    yield SimpleNamespace(cfg=cfg, clock=clock, listing=listing, shell=shell, load=load)
    versions.clear_observation_cache()


def assert_cause(cause, callback):
    with pytest.raises(versions.MinuteVersionError) as raised:
        callback()
    assert raised.value.cause == cause
    assert "fixture-private" not in str(raised.value)
    assert SERIAL_A not in str(raised.value)
    assert SERIAL_B not in str(raised.value)
    return raised.value


def test_public_api_keyword_contracts():
    assert issubclass(versions.MinuteVersionError, RuntimeError)
    for function, names in (
        (versions.observe_installed_version, ["force_refresh"]),
        (versions.client_version, ["force_refresh", "require_reviewed"]),
    ):
        signature = inspect.signature(function)
        assert list(signature.parameters) == names
        for parameter in signature.parameters.values():
            assert parameter.kind == inspect.Parameter.KEYWORD_ONLY
            assert parameter.default is False
    assert list(inspect.signature(versions.require_reviewed_version).parameters) == ["snapshot"]
    assert not inspect.signature(versions.clear_observation_cache).parameters


@pytest.mark.parametrize("name,code", [("1.29.0", 1004038), ("1.30.0", 1004039)])
def test_public_installed_snapshot_and_reviewed_pairs(isolated, name, code):
    isolated.shell.return_value = completed(package_dump(name, str(code)))
    snap = versions.observe_installed_version()
    assert snap == {
        "serial": SERIAL_A, "package": PACKAGE, "version_name": name,
        "version_code": code, "last_update_time": UPDATED, "observed_at_monotonic": 100.0,
    }
    assert versions.require_reviewed_version(snap) == name
    assert versions.client_version(require_reviewed=True) == name
    isolated.shell.assert_called_once_with(SERIAL_A, f"dumpsys package {PACKAGE}", timeout=15)


def test_last_update_time_optional_and_valid_fraction(isolated):
    isolated.shell.return_value = completed(package_dump(updated=None))
    assert "last_update_time" not in versions.observe_installed_version()
    isolated.shell.return_value = completed(package_dump(updated=UPDATED + ".123456789"))
    assert versions.observe_installed_version(force_refresh=True)["last_update_time"] == UPDATED + ".123456789"


@pytest.mark.parametrize("name", [
    "", "1.30", "v1.30.0", "01.30.0", "1.030.0", "1.30.00", "1.30.0 ",
    " 1.30.0", "1.30.0;id", "1.30.0-01", "1.30.0-rc.01", "1.30.0+", "1.30.0-",
])
def test_invalid_semver_is_rejected(isolated, name):
    isolated.shell.return_value = completed(package_dump(name))
    assert_cause("invalid_observation", versions.observe_installed_version)


@pytest.mark.parametrize("name", ["0.0.0", "1.31.0", "1.30.0-rc.1", "1.30.0+fixture.01"])
def test_unknown_semver_is_diagnostic_not_automatically_reviewed(isolated, name):
    isolated.shell.return_value = completed(package_dump(name))
    assert versions.client_version() == name
    assert_cause("unreviewed_version", lambda: versions.client_version(require_reviewed=True))


@pytest.mark.parametrize("code", ["", "0", "-1", "+1004039", "01004039", "1.0", "1004039x",
                                     "1004039;id", " 1004039", "1004039 unexpected", "9" * 20])
def test_invalid_positive_version_code_is_rejected(isolated, code):
    isolated.shell.return_value = completed(package_dump(code=code))
    assert_cause("invalid_observation", versions.observe_installed_version)


@pytest.mark.parametrize("raw", [
    "", None, b"versionName=1.30.0", "versionName=1.30.0\nversionCode=1004039",
    "Unable to find package: fixture.absent",
    package_dump(package="fixture.wrong.package"),
    package_dump().replace("    versionName=1.30.0\n", ""),
    package_dump().replace("    versionCode=1004039 minSdk=24 targetSdk=36\n", ""),
    package_dump().replace("installed=true", "installed=false"),
    package_dump().replace("    User 0: installed=true hidden=false stopped=false\n", ""),
    package_dump() + package_dump(),
    package_dump().replace("    versionName=1.30.0", "    versionName=1.30.0\n    versionName=1.29.0"),
    package_dump().replace("    versionCode=", "    versionCode=1004038\n    versionCode="),
    package_dump(updated="2026-02-31 03:04:05"),
    package_dump(updated="fixture-private/path"),
    package_dump().replace("Queries:\n", f"Queries:\n    lastUpdateTime={UPDATED}\n"),
])
def test_missing_uninstalled_wrong_or_ambiguous_package_data(isolated, raw):
    result = completed("")
    result.stdout = raw
    isolated.shell.return_value = result
    # The unrelated timestamp after the Package block must NOT contaminate it.
    if isinstance(raw, str) and raw.endswith(f"Queries:\n    lastUpdateTime={UPDATED}\n"):
        assert versions.observe_installed_version()["last_update_time"] == UPDATED
    else:
        assert_cause("invalid_observation", versions.observe_installed_version)


@pytest.mark.parametrize("returncode", [1, -1, None, False, "0"])
def test_nonzero_or_malformed_returncode_never_authorizes_stdout(isolated, returncode):
    isolated.shell.return_value = completed(returncode=returncode)
    assert_cause("adb_failed", versions.observe_installed_version)


@pytest.mark.parametrize("phase", ["inventory", "pick", "shell"])
@pytest.mark.parametrize("error,cause", [
    (subprocess.TimeoutExpired("fixture-private-command", 15, stderr="fixture-private-stderr"), "adb_timeout"),
    (TimeoutError("fixture-private-timeout"), "adb_timeout"),
    (FileNotFoundError("fixture-private/path"), "adb_unavailable"),
    (PermissionError("fixture-private/path"), "adb_failed"),
])
def test_transport_failures_are_sanitized_and_have_stable_cause(isolated, monkeypatch, phase, error, cause):
    if phase == "inventory":
        isolated.listing.side_effect = error
    elif phase == "pick":
        monkeypatch.setattr(adb, "pick_device", Mock(side_effect=error))
    else:
        isolated.shell.side_effect = error
    result = assert_cause(cause, versions.observe_installed_version)
    assert result.__suppress_context__
    assert "fixture-private" not in repr(result)


@pytest.mark.parametrize("devices", [[], [{"serial": SERIAL_A, "state": "offline"}],
                                       [{"serial": SERIAL_A, "state": "unauthorized"}]])
def test_missing_or_unauthorized_device_has_no_shell(isolated, devices):
    isolated.listing.return_value = devices
    assert_cause("device_unavailable", versions.observe_installed_version)
    isolated.shell.assert_not_called()


def test_multiple_authorized_devices_require_selection_even_when_legacy_would_pick_first(isolated, monkeypatch):
    isolated.listing.return_value = [{"serial": SERIAL_A}, {"serial": SERIAL_B}]
    pick = Mock(return_value=SERIAL_A)
    monkeypatch.setattr(adb, "pick_device", pick)
    assert_cause("device_ambiguous", versions.observe_installed_version)
    pick.assert_not_called()
    isolated.shell.assert_not_called()


def test_android_serial_and_explicit_serial_precedence_and_changes(isolated, monkeypatch):
    isolated.listing.return_value = [{"serial": SERIAL_A}, {"serial": SERIAL_B}]
    monkeypatch.setenv("ANDROID_SERIAL", SERIAL_A)
    assert versions.observe_installed_version()["serial"] == SERIAL_A
    monkeypatch.setenv("QMONEY_APPCHECK_ADB_SERIAL", SERIAL_B)
    assert versions.observe_installed_version()["serial"] == SERIAL_B
    assert isolated.shell.call_count == 2
    monkeypatch.setenv("QMONEY_APPCHECK_ADB_SERIAL", "fixture-missing-device")
    assert_cause("device_unavailable", versions.observe_installed_version)
    assert isolated.shell.call_count == 2  # no fallback to ANDROID_SERIAL or first device
    monkeypatch.delenv("QMONEY_APPCHECK_ADB_SERIAL")
    monkeypatch.setenv("ANDROID_SERIAL", SERIAL_B)
    assert versions.observe_installed_version()["serial"] == SERIAL_B
    assert isolated.shell.call_count == 3


def test_configuration_exclusions_and_physical_preference(isolated):
    isolated.listing.return_value = [{"serial": SERIAL_A}, {"serial": SERIAL_B}, {"serial": "emulator-5554"}]
    isolated.cfg["exclude_adb_serials"] = [SERIAL_B]
    assert versions.observe_installed_version()["serial"] == SERIAL_A
    isolated.cfg["prefer_physical_device"] = False
    assert_cause("device_ambiguous", versions.observe_installed_version)
    isolated.cfg["exclude_adb_serials"] = [SERIAL_A, "emulator-5554"]
    assert versions.observe_installed_version()["serial"] == SERIAL_B


def test_exclusions_are_not_bypassed_by_explicit_selection(isolated, monkeypatch):
    isolated.cfg["exclude_adb_serials"] = [SERIAL_A]
    monkeypatch.setenv("ANDROID_SERIAL", SERIAL_A)
    assert_cause("device_unavailable", versions.observe_installed_version)
    isolated.shell.assert_not_called()


@pytest.mark.parametrize("package", [None, "", "com.example;id", "com.example app", "../fixture.apk",
                                       "com.example\nfixture", "-bad.package", "single", "com..example"])
def test_package_is_safe_before_any_adb(isolated, package):
    isolated.cfg["package"] = package
    assert_cause("invalid_package", versions.observe_installed_version)
    isolated.listing.assert_not_called()
    isolated.shell.assert_not_called()


@pytest.mark.parametrize("serial", ["../fixture", "-fixture", "fixture;id", "fixture name", "fixture\nother"])
def test_serial_is_safe_before_any_adb(isolated, monkeypatch, serial):
    monkeypatch.setenv("ANDROID_SERIAL", serial)
    assert_cause("invalid_selection", versions.observe_installed_version)
    isolated.listing.assert_not_called()


@pytest.mark.parametrize("devices,cause", [
    (None, "adb_failed"), ([None], "adb_failed"), ([{"serial": "fixture;id"}], "invalid_selection"),
    ([{"serial": SERIAL_A}, {"serial": SERIAL_A}], "device_ambiguous"),
])
def test_malformed_or_duplicate_inventory_cannot_be_authority(isolated, devices, cause):
    isolated.listing.return_value = devices
    assert_cause(cause, versions.observe_installed_version)
    isolated.shell.assert_not_called()


def test_pick_device_is_late_replaceable_and_must_confirm_the_selected_device(isolated, monkeypatch):
    versions.observe_installed_version()
    monkeypatch.setattr(adb, "pick_device", Mock(return_value=SERIAL_B))
    assert_cause("invalid_selection", versions.observe_installed_version)
    assert not versions._cache


def test_selection_disconnect_between_inventory_and_pick_is_sanitized(isolated, monkeypatch):
    monkeypatch.setattr(adb, "pick_device", Mock(side_effect=RuntimeError("fixture-private-serial/path")))
    assert_cause("device_unavailable", versions.observe_installed_version)
    isolated.shell.assert_not_called()


@pytest.mark.parametrize("update", [
    lambda monkeypatch: monkeypatch.setattr(config, "ROOT", Path("fixture-profile-b")),
    lambda monkeypatch: monkeypatch.setattr(config, "DATA_DIR", Path("fixture-profile-b/data")),
    lambda monkeypatch: monkeypatch.setenv("QMONEY_USER_ROOT", "fixture-profile-b"),
    lambda monkeypatch: monkeypatch.setenv("LOCALAPPDATA", "fixture-local-b"),
    lambda monkeypatch: monkeypatch.setattr(roglife_config, "config_path", lambda: Path("fixture-config-b.json")),
])
def test_profile_root_or_config_path_change_never_inherits_observation(isolated, monkeypatch, update):
    assert versions.observe_installed_version()["version_name"] == "1.30.0"
    update(monkeypatch)
    isolated.shell.return_value = completed(package_dump("1.29.0", "1004038"))
    assert versions.observe_installed_version()["version_name"] == "1.29.0"
    assert isolated.shell.call_count == 2


def test_package_and_automatic_device_change_never_inherit_observation(isolated):
    versions.observe_installed_version()
    isolated.cfg["package"] = "fixture.other.package"
    isolated.shell.return_value = completed(package_dump(package="fixture.other.package"))
    assert versions.observe_installed_version()["package"] == "fixture.other.package"
    isolated.listing.return_value = [{"serial": SERIAL_B}]
    assert versions.observe_installed_version()["serial"] == SERIAL_B
    assert isolated.shell.call_count == 3


def test_cache_ttl_boundaries_return_copies_and_force_refresh(isolated):
    original = versions.observe_installed_version()
    original["version_name"] = "fixture-mutated"
    isolated.clock.now = 111.999
    assert versions.observe_installed_version()["version_name"] == "1.30.0"
    assert isolated.shell.call_count == 1
    isolated.clock.now = 112.0
    isolated.shell.return_value = completed(package_dump("1.31.0", "1004040"))
    assert versions.observe_installed_version()["version_name"] == "1.31.0"
    assert isolated.shell.call_count == 2
    isolated.shell.return_value = completed(package_dump("1.32.0", "1004041"))
    assert versions.observe_installed_version(force_refresh=True)["version_name"] == "1.32.0"
    assert isolated.shell.call_count == 3
    isolated.clock.now = 50.0  # defensive against a replacement clock going backwards
    versions.observe_installed_version()
    assert isolated.shell.call_count == 4


@pytest.mark.parametrize("name,code", [("1.31.0", "1004039"), ("1.30.0", "1004040")])
def test_version_name_or_code_change_is_reread_and_blocked_at_review_gate(isolated, name, code):
    versions.client_version(require_reviewed=True)
    isolated.shell.return_value = completed(package_dump(name, code))
    snap = versions.observe_installed_version(force_refresh=True)
    assert (snap["version_name"], snap["version_code"]) == (name, int(code))
    assert_cause("unreviewed_version", lambda: versions.require_reviewed_version(snap))


@pytest.mark.parametrize("failure", ["shell", "timeout", "inventory", "ambiguous", "invalid"])
def test_error_evicts_cache_and_never_serves_stale_even_before_ttl(isolated, failure):
    versions.observe_installed_version()
    if failure == "shell":
        isolated.shell.return_value = completed(returncode=1)
    elif failure == "timeout":
        isolated.shell.side_effect = TimeoutError("fixture-private")
    elif failure == "inventory":
        isolated.listing.return_value = []
    elif failure == "ambiguous":
        isolated.listing.return_value = [{"serial": SERIAL_A}, {"serial": SERIAL_B}]
    else:
        isolated.shell.return_value = completed("fixture-invalid")
    with pytest.raises(versions.MinuteVersionError):
        versions.observe_installed_version(force_refresh=failure not in {"inventory", "ambiguous"})
    assert not versions._cache
    isolated.shell.side_effect = None
    isolated.shell.return_value = completed(package_dump("1.29.0", "1004038"))
    isolated.listing.return_value = [{"serial": SERIAL_A}]
    assert versions.observe_installed_version()["version_name"] == "1.29.0"


def test_config_failure_evicts_memory_without_exposing_path(isolated):
    versions.observe_installed_version()
    isolated.load.side_effect = FileNotFoundError("fixture-private/path")
    assert_cause("configuration_unavailable", versions.observe_installed_version)
    assert not versions._cache
    isolated.load.side_effect = lambda: dict(isolated.cfg)
    versions.observe_installed_version()
    assert isolated.shell.call_count == 2


@pytest.mark.parametrize("cfg", [None, [], {"package": PACKAGE, "prefer_physical_device": "yes"},
                                   {"package": PACKAGE, "exclude_adb_serials": "fixture"}])
def test_malformed_config_is_not_coerced_to_defaults(isolated, cfg):
    isolated.load.side_effect = None
    isolated.load.return_value = cfg
    assert_cause("configuration_unavailable", versions.observe_installed_version)
    isolated.shell.assert_not_called()


def test_clear_observation_cache_only_clears_this_modules_memory(isolated):
    versions.observe_installed_version()
    versions.clear_observation_cache()
    assert not versions._cache
    versions.observe_installed_version()
    assert isolated.shell.call_count == 2


def observe_waiter(monkeypatch):
    """Signal only when a follower really joined the existing flight (no sleeps)."""
    with versions._lock:
        flight = next(iter(versions._flights.values()))
    joined = threading.Event()
    wait = flight.done.wait

    def observed_wait(*args, **kwargs):
        joined.set()
        return wait(*args, **kwargs)

    monkeypatch.setattr(flight.done, "wait", observed_wait)
    return joined


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_concurrent_readers_singleflight_share_fresh_result_or_error(isolated, monkeypatch, force, fail):
    # Force concurrency must never inherit the successful old observation.
    if force:
        versions.observe_installed_version()
    entered, release = threading.Event(), threading.Event()

    def slow_read(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        if fail:
            raise TimeoutError("fixture-private")
        return completed(package_dump("1.31.0", "1004040"))

    isolated.shell.side_effect = slow_read
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(versions.observe_installed_version, force_refresh=force)
        try:
            assert entered.wait(5)
            joined = observe_waiter(monkeypatch)
            second = pool.submit(versions.observe_installed_version, force_refresh=force)
            assert joined.wait(5)
        finally:
            release.set()
        if fail:
            for future in (first, second):
                assert_cause("adb_timeout", lambda: future.result(timeout=5))
            assert not versions._cache
        else:
            a, b = first.result(timeout=5), second.result(timeout=5)
            assert a == b
            assert a is not b
            assert a["version_name"] == "1.31.0"
    assert isolated.shell.call_count == (2 if force else 1)


def test_force_joining_nonforced_cache_flight_must_perform_live_read(isolated, monkeypatch):
    versions.observe_installed_version()
    entered, release = threading.Event(), threading.Event()
    real_pick = adb.pick_device

    def slow_pick(cfg, serial):
        entered.set()
        assert release.wait(5)
        return real_pick(cfg, serial)

    monkeypatch.setattr(adb, "pick_device", slow_pick)
    isolated.shell.return_value = completed(package_dump("1.31.0", "1004040"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        cached = pool.submit(versions.observe_installed_version)
        try:
            assert entered.wait(5)
            joined = observe_waiter(monkeypatch)
            forced = pool.submit(versions.observe_installed_version, force_refresh=True)
            assert joined.wait(5)
        finally:
            release.set()
        assert cached.result(timeout=5)["version_name"] == "1.30.0"
        assert forced.result(timeout=5)["version_name"] == "1.31.0"
    assert isolated.shell.call_count == 2


def test_reset_during_live_flight_cannot_repopulate_or_evict_new_generation(isolated):
    entered, release = threading.Event(), threading.Event()

    def slow_read(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return completed(package_dump("1.29.0", "1004038"))

    isolated.shell.side_effect = slow_read
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(versions.observe_installed_version)
        try:
            assert entered.wait(5)
            versions.clear_observation_cache()
            isolated.shell.side_effect = None
            fresh = versions.observe_installed_version()
            assert fresh["version_name"] == "1.30.0"
        finally:
            release.set()
        assert_cause("observation_reset", lambda: old.result(timeout=5))
    assert versions.observe_installed_version() == fresh
    assert isolated.shell.call_count == 2


def test_independent_profile_context_does_not_wait_for_old_profiles_flight(isolated, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow_read(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return completed(package_dump("1.29.0", "1004038"))

    isolated.shell.side_effect = slow_read
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(versions.observe_installed_version, force_refresh=True)
        try:
            assert entered.wait(5)
            monkeypatch.setattr(config, "ROOT", Path("fixture-profile-b"))
            isolated.shell.side_effect = None
            assert versions.observe_installed_version(force_refresh=True)["version_name"] == "1.30.0"
        finally:
            release.set()
        assert old.result(timeout=5)["version_name"] == "1.29.0"
    assert versions.observe_installed_version()["version_name"] == "1.30.0"


@pytest.mark.parametrize("force,reviewed", [(False, False), (True, False), (False, True), (True, True)])
def test_nondevice_uses_only_config_version_without_loading_config_or_adb(isolated, monkeypatch, force, reviewed):
    monkeypatch.setattr(provider, "device_mode_enabled", lambda: False)
    monkeypatch.setattr(config, "APP_VERSION", "fixture-config-version")
    monkeypatch.setenv("MINUTE_APP_VERSION", "9.99.0")
    monkeypatch.setenv("QMONEY_APP_VERSION", "9.99.1")
    assert versions.client_version(force_refresh=force, require_reviewed=reviewed) == "fixture-config-version"
    isolated.load.assert_not_called()
    isolated.listing.assert_not_called()
    isolated.shell.assert_not_called()


def test_device_observation_never_uses_config_or_env_version_as_fallback(isolated, monkeypatch):
    monkeypatch.setattr(config, "APP_VERSION", "1.29.0")
    monkeypatch.setenv("MINUTE_APP_VERSION", "1.30.0")
    isolated.listing.return_value = []
    assert_cause("device_unavailable", versions.client_version)
    assert_cause("device_unavailable", lambda: versions.client_version(require_reviewed=True))


@pytest.mark.parametrize("snapshot,cause", [
    (None, "invalid_observation"), ({}, "invalid_observation"),
    ({"version_name": "1.30.0", "version_code": True}, "invalid_observation"),
    ({"version_name": "1.30.0", "version_code": "1004039"}, "invalid_observation"),
    ({"version_name": "1.30.0", "version_code": 0}, "invalid_observation"),
    ({"version_name": "fixture-private", "version_code": 1004039}, "invalid_observation"),
    ({"package": "fixture.other", "version_name": "1.30.0", "version_code": 1004039}, "unreviewed_version"),
])
def test_review_gate_is_strict_without_any_transport(isolated, snapshot, cause):
    assert_cause(cause, lambda: versions.require_reviewed_version(snapshot))
    isolated.load.assert_not_called()
    isolated.listing.assert_not_called()
    isolated.shell.assert_not_called()


@pytest.mark.parametrize("name,code,allowed", [
    ("1.29.0", "1004038", True), ("1.30.0", "1004039", True),
    ("1.30.0", "1004040", False), ("1.31.0", "1004040", False),
    ("1.29.0", "1004039", False), ("1.30.0", "1004038", False),
])
def test_admission_callback_runs_only_after_forced_exact_review_pair(isolated, name, code, allowed):
    versions.observe_installed_version()  # stale-but-within-TTL reviewed version
    isolated.shell.return_value = completed(package_dump(name, code))
    callback = Mock(return_value="fixture-admitted")

    def admission():
        version = versions.client_version(force_refresh=True, require_reviewed=True)
        return callback(version)

    if allowed:
        assert admission() == "fixture-admitted"
        callback.assert_called_once_with(name)
    else:
        error = assert_cause("unreviewed_version", admission)
        assert "integração" in str(error) and "número" in str(error)
        callback.assert_not_called()
    assert isolated.shell.call_count == 2


def test_unknown_error_constructor_input_cannot_leak_arbitrary_details():
    for cause in ("fixture-private/path", None, {"fixture-private": "detail"}):
        error = versions.MinuteVersionError(cause)
        assert error.cause == "invalid_observation"
        assert "fixture-private" not in str(error)
