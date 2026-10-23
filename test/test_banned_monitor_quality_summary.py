"""Offline quality-summary contracts with explicit, test-local dependencies."""
import copy
import json
from contextlib import ExitStack
from unittest.mock import patch

import pytest

from moneymin import minute_api
from moneymin.mobile import minute_version
from moneymin.web import banned_monitor as monitor


EMAIL = "archived@supply.claru.ai"


@pytest.fixture
def api():
    """Mock tuple sign-in, detailed GETs and observed version; forbid persistence."""
    key = monitor.org_policy.target_org_key(EMAIL)
    profile = {"disabled": False, "organizations": [{"resourceKey": key, "disabled": False}]}
    with ExitStack() as stack:
        signin = stack.enter_context(patch.object(minute_api, "_request", return_value=(
            200, json.dumps({"idToken": "ephemeral-test-token"})) ))
        get = stack.enter_context(patch.object(minute_api, "_request_detailed"))
        version = stack.enter_context(patch.object(minute_version, "client_version", return_value="1.30.0"))
        # These higher-level operations persist credentials or perform mutations.
        for name in ("login", "save_json"):
            stack.enter_context(patch.object(minute_api, name, side_effect=AssertionError(name)))
        get.side_effect = [response(profile), response({"userState": "active", "overall": "great"})]
        yield profile, get, signin, version, key


def response(payload, status=200, headers=None):
    return minute_api.HttpResponse(status, json.dumps(payload), headers or {})


def inspect():
    return monitor._inspect_account({"email": EMAIL, "password": "fixture-password"})


def test_correct_route_version_and_ephemeral_auth_no_mutations(api, tmp_path):
    profile, get, signin, version, key = api
    (tmp_path / "tokens.json").write_text('{"sentinel": "tokens"}')
    (tmp_path / "banned_accounts.json").write_text('{"sentinel": "archive"}')
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    row = {"email": EMAIL, "password": "fixture-password", "monitor": {"balance": None}}
    original = copy.deepcopy(row)
    with patch.object(monitor.config, "DATA_DIR", tmp_path), \
         patch.object(monitor.crowtado, "login", side_effect=AssertionError("no Crowtado for Claru")):
        result = monitor._inspect_account(row)
    assert result["status"] == "unbanned"
    assert row == original
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert signin.call_count == 1
    assert signin.call_args.args == (minute_api._SIGNIN_URL, "POST")
    assert [call.args[0] for call in get.call_args_list] == [
        monitor.config.BASE_URL + "/api/v1/users/me",
        monitor.config.BASE_URL + f"/api/v1/organizations/{key}/quality-summary",
    ]
    for call in get.call_args_list:
        assert call.kwargs["headers"]["X-App-Version"] == "1.30.0"
        assert call.kwargs["headers"]["Authorization"] == "Bearer ephemeral-test-token"
        assert call.kwargs.get("method", "GET") == "GET"
    version.assert_called_once_with(force_refresh=False, require_reviewed=False)
    assert "ephemeral-test-token" not in json.dumps(result)


@pytest.mark.parametrize("state,expected", [
    ("active", "unbanned"), ("on_hold", "access_paused"),
    ("inactive", "access_paused"), ("unknown", "inconclusive"),
    (None, "inconclusive"), (True, "inconclusive"), ({}, "inconclusive"),
])
def test_user_states(api, state, expected):
    profile, get, *_ = api
    get.side_effect = [response(profile), response({"userState": state, "overall": "great"})]
    assert monitor.check_status(EMAIL, "fixture-password")["status"] == expected


@pytest.mark.parametrize("where,expected", [("account", "banned"), ("organization", "access_paused")])
def test_confirmed_profile_restrictions_skip_quality(api, where, expected):
    profile, get, *_ = api
    if where == "account":
        profile["disabled"] = True
    else:
        profile["organizations"][0]["disabled"] = True
    get.side_effect = [response(profile)]
    assert monitor.check_status(EMAIL, "fixture-password")["status"] == expected
    assert get.call_count == 1


@pytest.mark.parametrize("stage", ["profile", "quality"])
@pytest.mark.parametrize("status,headers,code", [
    (401, {}, "authentication"), (403, {}, "forbidden"),
    (403, {"X-Blocked-Reason": "app_check"}, "app_check"),
    (403, {"X-Blocked-Reason": "device"}, "device"),
    (404, {}, "unknown"), (408, {}, "timeout"),
    (429, {"Retry-After": "23"}, "rate_limit"), (503, {}, "service"),
    (-1, {}, "network"),
])
def test_http_failures_are_inconclusive_with_cause(api, stage, status, headers, code):
    profile, get, *_ = api
    failure = response({}, status, headers)
    get.side_effect = [failure] if stage == "profile" else [response(profile), failure]
    result = inspect()
    assert result["status"] == "inconclusive"
    issue = result["providers"]["minute"]["issue"]
    assert issue["code"] == code
    assert not issue["restriction_confirmed"]
    assert result["restricted_providers"] == []


@pytest.mark.parametrize("stage", ["profile", "quality"])
@pytest.mark.parametrize("text", ["not json", "null", "[]", '"active"', "{}", '{"userState":"future","overall":"great"}'])
def test_invalid_or_unknown_payload_is_inconclusive(api, stage, text):
    profile, get, *_ = api
    invalid = minute_api.HttpResponse(200, text, {})
    get.side_effect = [invalid] if stage == "profile" else [response(profile), invalid]
    assert inspect()["status"] == "inconclusive"


@pytest.mark.parametrize("text", ["null", "[]", "{}", "not json"])
def test_invalid_auth_never_reads_profile(api, text):
    _, get, signin, *_ = api
    signin.return_value = (200, text)
    assert inspect()["status"] == "inconclusive"
    get.assert_not_called()


def test_firebase_confirmed_disabled_stays_banned(api):
    _, get, signin, *_ = api
    signin.return_value = (400, json.dumps({"error": {"message": "USER_DISABLED"}}))
    assert inspect()["status"] == "banned"
    get.assert_not_called()


def test_version_failure_is_inconclusive_without_fallback(api):
    _, get, _, version, _ = api
    version.side_effect = minute_version.MinuteVersionError("device_unavailable")
    assert inspect()["status"] == "inconclusive"
    get.assert_not_called()


def test_retry_after_survives_detailed_get(api):
    profile, get, *_ = api
    get.side_effect = [response(profile), response({}, 429, {"Retry-After": "23"})]
    with pytest.raises(minute_api.AuthError) as caught:
        monitor.check_status(EMAIL, "fixture-password")
    assert caught.value.retry_after_seconds == 23


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, -1])
def test_unconfirmed_signin_failures_remain_inconclusive(api, status):
    _, get, signin, *_ = api
    signin.return_value = (status, "{}")
    assert inspect()["status"] == "inconclusive"
    get.assert_not_called()


def test_confirmed_profile_ban_remains_disabled_in_aggregate(api):
    profile, get, *_ = api
    profile["disabled"] = True
    get.side_effect = [response(profile)]
    result = inspect()
    assert result["status"] == "banned"
    assert result["providers"]["minute"]["issue"]["restriction_confirmed"]
    assert get.call_count == 1


def test_unknown_profile_disabled_flag_cannot_confirm_active(api):
    profile, get, *_ = api
    profile.pop("disabled")
    get.side_effect = [response(profile), response({"userState": "active"})]
    assert inspect()["status"] == "inconclusive"
