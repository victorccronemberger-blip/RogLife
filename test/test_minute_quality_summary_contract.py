"""Offline quality-summary contracts; synthetic fixtures, no provider/device access."""
import json
from unittest.mock import Mock

import pytest

from moneymin import minute_api as api


ORG = "target-org"
ROUTE = f"/api/v1/organizations/{ORG}/quality-summary"
PROFILE = {
    "disabled": False,
    "organizations": [
        {"resourceKey": "other-org", "disabled": True},
        {"resourceKey": ORG, "disabled": False, "cameraSources": ["built-in"]},
    ],
}
ACTIVE = {"userState": "active", "overall": "great", "scores": []}
BODY = {"duration_ms": 2000, "recorded_at": "fixture",
        "meta": {"cameras": [{"source": "builtin"}]}}


@pytest.fixture
def session(monkeypatch):
    sess = api.Session({"email": "quality@example.invalid", "idToken": "fixture"})
    sess._live = True
    monkeypatch.setattr(sess, "me", Mock(return_value=PROFILE))
    monkeypatch.setattr(sess, "warmup", Mock())
    monkeypatch.setattr(sess, "_check_local_restrictions", Mock())
    monkeypatch.setattr(api, "require_supported_client", Mock())
    monkeypatch.setattr(sess, "_check_recording_geo", Mock())
    sess.recording_policy = Mock()
    sess.device_camera_allowed = True
    return sess


def respond(monkeypatch, session, status=200, text=None, headers=None, profile=None):
    response = api.HttpResponse(status, json.dumps(ACTIVE) if text is None else text, headers or {})

    def detailed(method, path, body=None):
        assert method == "GET"
        if path == "/api/v1/users/me":
            return api.HttpResponse(200, json.dumps(profile or PROFILE), {})
        assert path == ROUTE  # no legacy route, guesses or fallback
        return response

    request = Mock(side_effect=detailed)
    monkeypatch.setattr(session, "request_detailed", request)
    return request


def gate(session, kind):
    if kind == "read":
        return session.checked_quality_state(ORG)
    if kind == "org":
        return session.org_state(ORG)
    if kind == "auth":
        return session.ensure_auth(org_key=ORG)
    return session._check_write_policy("POST", f"/api/v1/uploads?org_key={ORG}", BODY)


@pytest.mark.parametrize("kind", ["read", "org", "auth", "write"])
def test_exact_route_active_allows_verified_access(session, monkeypatch, kind):
    request = respond(monkeypatch, session)
    result = gate(session, kind)
    assert any(call.args == ("GET", ROUTE) for call in request.call_args_list)
    if kind == "read":
        assert result == ACTIVE
    elif kind == "org":
        assert result["blocked"] is False  # unrelated disabled org does not block
        assert result["userState"] == "active"
        assert result["overall"] == "great"
    elif kind == "write":
        session._check_recording_geo.assert_called_once_with(ORG)
        session.recording_policy.validate_duration.assert_called_once_with(2000)


@pytest.mark.parametrize("state", ["on_hold", "inactive"])
@pytest.mark.parametrize("kind", ["org", "auth", "write"])
def test_real_pauses_remain_paused(session, monkeypatch, state, kind):
    respond(monkeypatch, session, text=json.dumps({"userState": state}))
    if kind == "org":
        result = gate(session, kind)
        assert result["blocked"] is True
        assert result["disabled"] is False
        assert result["userState"] == state
    else:
        with pytest.raises(api.AuthError) as caught:
            gate(session, kind)
        assert caught.value.account_issue_code == "access_paused"
        session._check_recording_geo.assert_not_called()
        if kind == "auth":
            session.warmup.assert_not_called()


@pytest.mark.parametrize("status,text,headers,code,retry", [
    (404, 'NotFound private-fixture', {}, "unknown", None),
    (401, 'private-fixture', {}, "authentication", None),
    (403, 'private-fixture', {}, "forbidden", None),
    (429, 'private-fixture', {"Retry-After": "17"}, "rate_limit", 17),
    (-1, 'ERRO: timed out private-fixture', {}, "timeout", None),
    (403, 'private-fixture', {"X-Blocked-Reason": "app_check"}, "app_check", None),
    (401, 'private-fixture', {"x-blocked-reason": "app_check"}, "app_check", None),
])
@pytest.mark.parametrize("kind", ["read", "org", "auth", "write"])
def test_failed_query_preserves_sanitized_cause_not_pause(
        session, monkeypatch, status, text, headers, code, retry, kind):
    request = respond(monkeypatch, session, status, text, headers)
    with pytest.raises(api.AuthError) as caught:
        gate(session, kind)
    error = caught.value
    assert error.account_issue_code == code
    assert error.account_issue_code != "access_paused"
    assert error.http_status == status
    assert error.retry_after_seconds == retry
    assert "private-fixture" not in str(error)
    assert error.remote_effect_possible is False
    assert sum(call.args == ("GET", ROUTE) for call in request.call_args_list) == 1
    session._check_recording_geo.assert_not_called()
    if kind == "auth":
        session.warmup.assert_not_called()


@pytest.mark.parametrize("text", ["broken", "[]", "null", "{}", '{"overall":"great"}',
                                   '{"userState":"unknown"}', '{"userState":"ACTIVE"}',
                                   '{"userState":true}', '{"userState":null}'])
@pytest.mark.parametrize("kind", ["read", "org", "auth", "write"])
def test_invalid_or_missing_state_never_authorizes_or_becomes_pause(session, monkeypatch, text, kind):
    respond(monkeypatch, session, text=text)
    with pytest.raises(api.AuthError) as caught:
        gate(session, kind)
    assert caught.value.account_issue_code == "invalid_response"
    session._check_recording_geo.assert_not_called()
    if kind == "auth":
        session.warmup.assert_not_called()


@pytest.mark.parametrize("disabled", ["account", "organization"])
@pytest.mark.parametrize("kind", ["org", "auth", "write"])
def test_confirmed_disabled_has_priority_over_unavailable_quality(session, monkeypatch, disabled, kind):
    profile = {"disabled": disabled == "account", "organizations": [
        {"resourceKey": ORG, "disabled": disabled == "organization"}]}
    session.me.return_value = profile
    request = respond(monkeypatch, session, 503, "unavailable", profile=profile)
    if kind == "org":
        result = gate(session, kind)
        assert result["blocked"] is True
        assert result["disabled"] is True
    else:
        with pytest.raises(api.AuthError) as caught:
            gate(session, kind)
        assert caught.value.account_issue_code == ("restricted" if disabled == "account" else "access_paused")
    assert not any(call.args == ("GET", ROUTE) for call in request.call_args_list)
    session._check_recording_geo.assert_not_called()


@pytest.mark.parametrize("status,text,expected", [
    (200, json.dumps(ACTIVE), ACTIVE), (404, "NotFound", {}),
    (200, "broken", {}), (200, "[]", {}),
])
def test_compatible_read_uses_exact_route_and_keeps_empty_failure_contract(session, monkeypatch, status, text, expected):
    get = Mock(return_value=(status, text))
    monkeypatch.setattr(session, "get", get)
    assert session.quality_state(ORG) == expected
    get.assert_called_once_with(ROUTE)


@pytest.mark.parametrize("kind", ["auth", "write"])
def test_defensive_unknown_from_org_state_is_not_access_paused(session, monkeypatch, kind):
    respond(monkeypatch, session)
    monkeypatch.setattr(session, "org_state", Mock(return_value={"blocked": False, "userState": "unknown"}))
    with pytest.raises(api.AuthError) as caught:
        gate(session, kind)
    assert caught.value.account_issue_code == "invalid_response"
    session._check_recording_geo.assert_not_called()
