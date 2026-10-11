"""Clerk account flags and fresh login validation with isolated provider replies."""
from unittest.mock import Mock, patch
from types import SimpleNamespace
import socket
import sys
import pytest
from moneymin import crowtado, hostinger_mail, registration_proxy
from datetime import datetime, timezone


@pytest.mark.parametrize("flag,expected", [("banned", "restricted"), ("locked", "account_locked")])
@pytest.mark.parametrize("where", ["response", "client"])
def test_explicit_account_flags_are_classified_without_conflating_lockout(flag, expected, where):
    body = {"response":{"status":"complete", "created_session_id":"fixture-session"}}
    user = {"banned":False,"locked":False,flag:True}
    if where == "response":
        body["response"]["user"] = user
    else:
        body["client"] = {"sessions":[{"id":"fixture-session","user":user}]}
    with patch.object(crowtado.CrowtadoSession,"_fapi",side_effect=[(200,{}),(200,body)]):
        with pytest.raises(crowtado.CrowtadoError) as error:
            crowtado.login("fixture@example.invalid","fixture-password")
    assert error.value.account_issue_code == expected
    if flag == "banned":
        assert error.value.provider_error_code == "user_banned"
    else:
        assert error.value.provider_error_code is None


def test_complete_login_with_clear_flags_remains_valid():
    body = {"response":{"status":"complete","created_session_id":"fixture-session","user":{"banned":False,"locked":False}}}
    with patch.object(crowtado.CrowtadoSession,"_fapi",side_effect=[(200,{}),(200,body)]):
        assert crowtado.login("fixture@example.invalid","fixture-password").session_id == "fixture-session"


def test_other_session_flags_do_not_mark_current_user_banned():
    crowtado._check_login_user_flags({"client":{"sessions":[{"id":"other-session","user":{"banned":True}}]}},"current-session")


def test_nonboolean_flags_are_inconclusive_rather_than_success():
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado._check_login_user_flags({"response":{"user":{"banned":"false"}}},"fixture-session")
    assert error.value.account_issue_code == "invalid_response"


def test_locked_flag_carries_only_valid_lockout_duration():
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado._check_login_user_flags(
            {"response": {"user": {"banned": False, "locked": True,
                                     "lockout_expires_in_seconds": 23}}}, "fixture-session")
    assert error.value.account_issue_code == "account_locked"
    assert error.value.retry_after_seconds == 23
    assert error.value.account_issue_code != "restricted"


def test_clerk_user_locked_error_is_temporary_and_parses_metadata():
    error = crowtado._remote_error("safe stage", 403, {
        "errors": [{"code": "user_locked", "long_message": "private provider text",
                    "meta": {"lockout_expires_in_seconds": 17}}]})
    assert error.account_issue_code == "account_locked"
    assert error.retry_after_seconds == 17
    assert error.provider_error_code == "user_locked"
    assert "private provider text" not in str(error)


def test_signup_response_records_rate_limit_without_provider_body():
    error = crowtado._signup_response_error(
        "https://clerk.crowtado.com/v1/client/sign_ups?secret=query", 429,
        {"errors": [{"code": "too_many_requests", "long_message": "private response"}]},
        {"Retry-After": "9"}, "signup_submission")
    assert error is not None
    assert error.account_issue_code == "rate_limit"
    assert error.retry_after_seconds == 9
    assert error.phase == "signup_submission"
    assert error.remote_effect_possible is True
    assert error.provider_error_code is None
    assert "private response" not in str(error)
    assert "secret=query" not in str(error)


def test_provider_error_code_is_whitelisted_and_does_not_retain_payload():
    error = crowtado._remote_error("safe", 401, {
        "errors": [{"code": "form_password_incorrect", "long_message": "sensitive text",
                    "meta": {"email": "secret@example.invalid", "token": "secret-token"}}]})
    assert error.provider_error_code == "form_password_incorrect"
    assert "sensitive text" not in str(error)
    assert "secret@example.invalid" not in str(error)
    assert "secret-token" not in str(error)

    unrecognized = crowtado._remote_error("safe", 400, {
        "errors": [{"code": "private_vendor_code", "long_message": "sensitive text"}]})
    assert unrecognized.provider_error_code is None
    assert "private_vendor_code" not in str(unrecognized)


def test_signup_response_ignores_non_signup_and_success_responses():
    assert crowtado._signup_response_error(
        "https://clerk.crowtado.com/v1/client/sign_ins", 429, {}, {}, "signup_submission") is None
    assert crowtado._signup_response_error(
        "https://clerk.crowtado.com/v1/client/sign_ups", 200, {}, {}, "signup_submission") is None


@pytest.mark.parametrize("url", [
    "https://evil.example/v1/client/sign_ups",
    "https://clerk.crowtado.com.evil.example/v1/client/sign_ups",
    "http://clerk.crowtado.com/v1/client/sign_ups",
    "https://user@clerk.crowtado.com/v1/client/sign_ups",
    "https://clerk.crowtado.com/other/v1/client/sign_ups",
    "https://clerk.crowtado.com/v1/client/sign_ups_other",
])
def test_nonofficial_signup_response_cannot_authorize_a_ban_or_read_a_body(url):
    payload = {"errors": [{"code": "user_banned"}]}
    assert crowtado._signup_response_error(url, 403, payload, {}, "signup_submission") is None
    response = Mock(url=url, status=403, headers={})
    response.json.side_effect = AssertionError("unrelated response must not be parsed")
    assert crowtado._signup_response_error_from_response(response, "signup_submission") is None
    response.json.assert_not_called()


@pytest.mark.parametrize("path", [
    "/v1/client/sign_ups", "/v1/client/sign_ups/fixture-id/attempt_verification",
])
def test_official_signup_and_email_verification_responses_retain_ban_authority(path):
    error = crowtado._signup_response_error(
        crowtado.CLERK_BASE + path, 403, {"errors": [{"code": "user_banned"}]}, {},
        "email_verification")
    assert error.account_issue_code == "restricted"
    assert error.provider_error_code == "user_banned"
    assert error.remote_effect_possible is True


def test_signup_429_keeps_cooldown_when_response_body_is_not_json():
    class Response:
        url = "https://clerk.crowtado.com/v1/client/sign_ups"
        status = 429
        headers = {"retry-after": "12"}

        @staticmethod
        def json():
            raise ValueError("body must not escape")

    error = crowtado._signup_response_error_from_response(Response(), "signup_submission")
    assert error is not None
    assert error.account_issue_code == "rate_limit"
    assert error.retry_after_seconds == 12
    assert "body must not escape" not in str(error)


def test_account_lock_does_not_trigger_browser_fallback():
    error = crowtado.CrowtadoError("A Crowtado bloqueou o acesso desta conta", code="account_locked")
    assert not crowtado.can_use_browser_fallback(error)


@pytest.mark.parametrize("url", [
    "https://www.crowtado.com/pt-BR/complete-profile?next=%2Fpt-BR%2Fdashboard",
    "https://www.crowtado.com/en/dashboard",
    "https://www.crowtado.com/pt-BR/dashboard/",
])
def test_signup_confirmation_accepts_only_official_post_signup_routes(url):
    assert crowtado._is_signup_confirmation_url(url)


@pytest.mark.parametrize("url", [
    "https://www.crowtado.com/pt-BR/sign-in",
    "https://www.crowtado.com/pt-BR/sign-up",
    "https://evil.example/pt-BR/dashboard",
    "http://www.crowtado.com/pt-BR/dashboard",
    "https://www.crowtado.com.evil.example/pt-BR/dashboard",
    "https://user@www.crowtado.com/pt-BR/dashboard",
    "javascript:alert(1)",
    "https://www.crowtado.com/dashboard",
])
def test_signup_confirmation_rejects_signin_external_and_undocumented_routes(url):
    assert not crowtado._is_signup_confirmation_url(url)


def test_signup_submission_and_verification_routes_are_origin_and_path_scoped():
    assert crowtado._is_signup_submission_url("https://www.crowtado.com/pt-BR/sign-up")
    assert crowtado._is_signup_verification_url(
        "https://www.crowtado.com/pt-BR/sign-up/verify-email-address")
    assert not crowtado._is_signup_submission_url("https://www.crowtado.com/pt-BR/sign-in")
    assert not crowtado._is_signup_verification_url(
        "https://www.crowtado.com/pt-BR/sign-up?next=verify")
    assert not crowtado._is_signup_verification_url(
        "https://evil.example/pt-BR/sign-up/verify-email-address")


@pytest.mark.parametrize(("status", "code", "retry_after"), [
    (400, "device", None),
    (403, "device", None),
    (429, "rate_limit", 11),
])
def test_signup_proof_failure_is_safe_metadata_only(status, code, retry_after):
    class Response:
        url = "https://www.crowtado.com/api/fraud/signup-fingerprint-proof?secret=query"
        headers = {"Retry-After": "11"}

        def __init__(self):
            self.status = status

        @staticmethod
        def json():
            raise AssertionError("proof body must not be read")

    error = crowtado._signup_proof_response_error_from_response(Response())
    assert error is not None
    assert error.account_issue_code == code
    assert error.http_status == status
    assert error.retry_after_seconds == retry_after
    assert error.phase == "signup_preparation"
    assert error.remote_effect_possible is False
    assert "secret=query" not in str(error)
    assert "proofId" not in str(error)


@pytest.mark.parametrize("url,status", [
    ("https://www.crowtado.com/api/fraud/signup-fingerprint-proof", 200),
    ("https://evil.example/api/fraud/signup-fingerprint-proof", 429),
    ("https://www.crowtado.com.evil.example/api/fraud/signup-fingerprint-proof", 400),
    ("https://www.crowtado.com/api/fraud/other", 400),
])
def test_signup_proof_observer_ignores_success_or_nonofficial_endpoint(url, status):
    assert crowtado._signup_proof_response_error(url, status, {}) is None


@pytest.mark.parametrize("month,year", [
    (True, 2000), (0, 2000), (13, 2000), (1, True), (1, 1899),
    (1, datetime.now(timezone.utc).year + 1),
])
def test_demographics_invalid_values_fail_before_login(month, year):
    with patch.object(crowtado, "login") as login:
        with pytest.raises(crowtado.CrowtadoError):
            crowtado.preencher_demografia("fixture@example.invalid", "fixture-password", month, year)
    login.assert_not_called()


def test_demographics_future_month_fails_before_login():
    now = datetime.now(timezone.utc)
    if now.month == 12:
        pytest.skip("UTC calendar year boundary has no future month in the current year")
    with patch.object(crowtado, "login") as login:
        with pytest.raises(crowtado.CrowtadoError):
            crowtado.preencher_demografia("fixture@example.invalid", "fixture-password", now.month + 1, now.year)
    login.assert_not_called()


def test_demographics_requires_complete_write_and_successful_readback():
    session = object()
    with patch.object(crowtado, "login", return_value=session), patch.object(
            crowtado, "_site_trpc", side_effect=[{"state": "incomplete"}, {"state": "complete"}, {"state": "complete"}]) as trpc:
        result = crowtado.preencher_demografia("fixture@example.invalid", "fixture-password", 1, 2000, "prefer_not_to_say")
    assert result["state"] == "complete"
    assert trpc.call_args_list[1].args[1:] == ("profile.saveDemographics", {
        "birthMonth": 1, "birthYear": 2000, "gender": "prefer_not_to_say"})
    assert trpc.call_args_list[2].args[1:] == ("profile.demographicsStatus", None)
    assert trpc.call_args_list[2].kwargs == {"method": "GET"}


@pytest.mark.parametrize("save_result", [{}, {"state": "underage_blocked"}, {"state": "incomplete"}])
def test_demographics_unconfirmed_save_never_reports_success(save_result):
    session = object()
    with patch.object(crowtado, "login", return_value=session), patch.object(
            crowtado, "_site_trpc", side_effect=[{"state": "incomplete"}, save_result]) as trpc:
        with pytest.raises(crowtado.CrowtadoError):
            crowtado.preencher_demografia("fixture@example.invalid", "fixture-password", 1, 2000)
    assert trpc.call_count == 2


def test_demographics_exempt_readback_is_accepted_only_after_complete_write():
    with patch.object(crowtado, "login", return_value=object()), patch.object(
            crowtado, "_site_trpc", side_effect=[{"state": "incomplete"}, {"state": "complete"}, {"state": "underage_blocked", "exempt": True}]):
        result = crowtado.preencher_demografia("fixture@example.invalid", "fixture-password", 1, 2000)
    assert result["exempt"] is True


def test_demographics_failed_readback_is_not_success():
    with patch.object(crowtado, "login", return_value=object()), patch.object(
            crowtado, "_site_trpc", side_effect=[{"state": "incomplete"}, {"state": "complete"}, {"state": "underage_blocked", "exempt": False}]):
        with pytest.raises(crowtado.CrowtadoError):
            crowtado.preencher_demografia("fixture@example.invalid", "fixture-password", 1, 2000)


def _install_fake_signup_browser(tmp_path, monkeypatch, *, response_timing=None, redirect=None,
                                 clerk_payload=None, clerk_status=403, clerk_headers=None,
                                 disabled_submit=False, incorrect_field=None, click_error=False):
    launched = []
    clock = [0.0]

    class Process:
        def __init__(self, args, **kwargs):
            self.terminated = False
            launched.append(list(args))
            page.process = self

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def bind(self, address):
            pass

        def getsockname(self):
            return ("127.0.0.1", 12345)

    class Response:
        url = "https://www.crowtado.com/api/fraud/signup-fingerprint-proof?proofId=fixture-secret"
        status = 403
        headers = {}

        @staticmethod
        def json():
            raise AssertionError("proof body must not be read")

    class Locator:
        @property
        def first(self):
            return self

        def count(self):
            return 1

        def check(self, **kwargs):
            if page.consent_error:
                raise RuntimeError("fixture consent unavailable")
            page.consent_checked = not page.consent_missed

        def is_checked(self):
            return page.consent_checked

        def press_sequentially(self, code, **kwargs):
            page.otp_submits += 1
            if page.otp_redirect:
                page.url = page.otp_redirect

    class Page:
        def __init__(self):
            self.response_handler = None
            self.url = "https://www.crowtado.com/pt-BR/sign-up"
            self.did_submit = False
            self.did_fill = False
            self.fields = {}
            self.submit_attempts = 0
            self.request_handler = None
            self.closed = False
            self.captcha_pending = False
            self.form_error = False
            self.front_count = 0
            self.on_wait = None
            self.on_submit = None
            self.otp_submits = 0
            self.otp_redirect = None
            self.fields_disabled = False
            self.consent_checked = False
            self.consent_error = False
            self.consent_missed = False

        def on(self, event, callback):
            if event == "response":
                self.response_handler = callback
            elif event == "request":
                self.request_handler = callback
            else:
                raise AssertionError(event)

        def goto(self, *args, **kwargs):
            if response_timing == "before_submit":
                self.response_handler(Response())

        def wait_for_timeout(self, ms):
            clock[0] += ms / 1000
            if self.on_wait:
                self.on_wait()

        def is_closed(self):
            return self.closed

        def is_enabled(self, selector):
            return not self.fields_disabled

        def bring_to_front(self):
            self.front_count += 1

        def locator(self, *_args, **_kwargs):
            return Locator()

        @staticmethod
        def query_selector(selector):
            return object() if selector == "#emailAddress-field" else None

        @staticmethod
        def wait_for_selector(*args, **kwargs):
            pass

        def fill(self, selector, value, **_kwargs):
            assert self.consent_checked, "terms gate still blocks the form"
            if self.fields_disabled:
                raise RuntimeError("fixture disabled field")
            self.did_fill = True
            self.fields[selector] = value

        def input_value(self, selector, **_kwargs):
            return "unexpected-private-value" if selector == incorrect_field else self.fields[selector]

        def evaluate(self, script, **_kwargs):
            return self.captcha_pending if "const challenge" in script else self.form_error

        def click(self, selector, *, trial=False, **_kwargs):
            assert selector == "button.cl-formButtonPrimary"
            if disabled_submit:
                raise RuntimeError("fixture disabled button timeout")
            if trial:
                return
            self.submit_attempts += 1
            self.did_submit = True
            self.request_handler(SimpleNamespace(method="POST", url="https://clerk.crowtado.com/v1/client/sign_ups"))
            if click_error:
                raise RuntimeError("fixture click response lost with private data")
            if redirect is not None:
                self.url = redirect
            if self.on_submit:
                self.on_submit()
            if response_timing == "after_submit":
                self.response_handler(Response())
            if response_timing in {"clerk_then_proof", "proof_then_clerk"}:
                clerk_response = SimpleNamespace(
                    url="https://clerk.crowtado.com/v1/client/sign_ups?secret=query",
                    status=clerk_status, headers=clerk_headers or {},
                    json=lambda: clerk_payload or {"errors": [
                        {"code": "user_banned", "long_message": "private provider text"}]} )

                responses = ((clerk_response, Response()) if response_timing == "clerk_then_proof"
                             else (Response(), clerk_response))
                for response in responses:
                    self.response_handler(response)

    page = Page()
    import time
    monkeypatch.setattr(crowtado, "time", SimpleNamespace(monotonic=lambda: clock[0], time=time.time))
    page.chrome_command = launched
    context = SimpleNamespace(
        clear_cookies=lambda: None, new_page=lambda: page, add_init_script=lambda _script: None)
    browser = SimpleNamespace(contexts=[context], close=lambda: None)
    playwright = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=lambda _url: browser))

    class PlaywrightManager:
        def __enter__(self):
            return playwright

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(crowtado, "CHROME_PROFILE", tmp_path / "chrome-profile")
    monkeypatch.setattr(crowtado, "_chrome_exe", lambda: "fake-chrome")
    monkeypatch.setattr(crowtado, "_wait_port", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(registration_proxy, "endpoint", lambda: None)
    monkeypatch.setattr(hostinger_mail, "max_uid", lambda _email: 0)
    monkeypatch.setattr(socket, "socket", lambda: FakeSocket())
    monkeypatch.setattr("subprocess.Popen", Process)
    monkeypatch.setitem(sys.modules, "playwright", SimpleNamespace(sync_api=SimpleNamespace(sync_playwright=PlaywrightManager)))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", SimpleNamespace(sync_playwright=PlaywrightManager))
    # A signup must never invoke an external CAPTCHA-solving service.
    from moneymin import human_behavior, nopecha_solver
    def forbidden_solver(*_args, **_kwargs):
        raise AssertionError("signup must wait for manual CAPTCHA verification")
    monkeypatch.setattr(nopecha_solver, "solve_captcha_if_present", forbidden_solver)
    monkeypatch.setattr(human_behavior, "session_warming", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(human_behavior, "handle_consent_checkbox", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(human_behavior, "random_delay", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(human_behavior, "human_type", lambda page, selector, value: page.fill(selector, value))
    # A coordinate click can return normally without activating a disabled button.
    monkeypatch.setattr(human_behavior, "human_click",
                        lambda page, selector: None if disabled_submit else page.click(selector))
    return page


def test_signup_disabled_submit_is_not_reported_as_an_uncertain_submission(tmp_path, monkeypatch):
    page = _install_fake_signup_browser(tmp_path, monkeypatch, disabled_submit=True)
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado.criar_conta("fixture@example.invalid", "fixture-password")
    assert error.value.account_issue_code == "signup_not_ready"
    assert error.value.remote_effect_possible is False
    assert not page.did_submit


@pytest.mark.parametrize("field", ["#emailAddress-field", "#password-field"])
def test_signup_field_readback_mismatch_stops_before_submit(tmp_path, monkeypatch, field):
    page = _install_fake_signup_browser(tmp_path, monkeypatch, incorrect_field=field,
                                        redirect="https://www.crowtado.com/en/dashboard")
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado.criar_conta("fixture@example.invalid", "fixture-password")
    assert error.value.account_issue_code == "signup_field_unconfirmed"
    assert error.value.remote_effect_possible is False
    assert not page.did_submit
    assert "fixture-password" not in str(error.value)
    assert "unexpected-private-value" not in str(error.value)


def test_signup_click_failure_is_not_repeated_and_remains_uncertain(tmp_path, monkeypatch):
    page = _install_fake_signup_browser(tmp_path, monkeypatch, click_error=True)
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado.criar_conta("fixture@example.invalid", "fixture-password")
    assert error.value.account_issue_code == "signup_click_unconfirmed"
    assert error.value.remote_effect_possible is True
    assert page.submit_attempts == 1
    assert "private data" not in str(error.value)


@pytest.mark.parametrize(("timing", "expected_effect", "expected_phase"), [
    ("before_submit", False, "signup_preparation"),
    ("after_submit", True, "signup_submission"),
])
def test_signup_proof_failure_tracks_remote_effect_at_event_time(tmp_path, monkeypatch,
                                                                  timing, expected_effect, expected_phase):
    page = _install_fake_signup_browser(tmp_path, monkeypatch, response_timing=timing)

    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado.criar_conta("fixture@example.invalid", "fixture-password")
    assert error.value.account_issue_code == "device"
    assert error.value.phase == expected_phase
    assert error.value.remote_effect_possible is expected_effect
    assert page.did_submit is expected_effect
    assert page.did_fill is expected_effect


@pytest.mark.parametrize("redirect", [
    "https://www.crowtado.com/pt-BR/sign-in",
    "https://evil.example/pt-BR/complete-profile",
])
def test_signup_redirect_to_signin_or_external_never_confirms(tmp_path, monkeypatch, redirect):
    _install_fake_signup_browser(tmp_path, monkeypatch, redirect=redirect)
    monkeypatch.setattr(hostinger_mail, "max_uid", lambda _email: 0)
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado.criar_conta("fixture@example.invalid", "fixture-password")
    assert error.value.account_issue_code == "signup_unconfirmed"
    assert error.value.phase == "signup_submission"
    assert error.value.remote_effect_possible is True


@pytest.mark.parametrize("redirect", [
    "https://www.crowtado.com/pt-BR/complete-profile?next=%2Fpt-BR%2Fdashboard",
    "https://www.crowtado.com/en/dashboard",
])
def test_signup_official_post_signup_redirect_returns_success(tmp_path, monkeypatch, redirect):
    page = _install_fake_signup_browser(tmp_path, monkeypatch, redirect=redirect)
    assert crowtado.criar_conta("fixture@example.invalid", "fixture-password") is None
    assert page.did_fill is True
    assert page.did_submit is True


@pytest.mark.parametrize("timing", ["clerk_then_proof", "proof_then_clerk"])
@pytest.mark.parametrize(("clerk_status", "clerk_code", "expected_code", "expected_provider"), [
    (403, "user_banned", "restricted", "user_banned"),
    (403, "user_locked", "account_locked", "user_locked"),
    (429, "too_many_requests", "rate_limit", None),
])
def test_explicit_clerk_signup_rejection_outranks_proof_failure(
        tmp_path, monkeypatch, timing, clerk_status, clerk_code, expected_code, expected_provider):
    _install_fake_signup_browser(
        tmp_path, monkeypatch, response_timing=timing, clerk_status=clerk_status,
        clerk_headers={"Retry-After": "17"},
        clerk_payload={"errors": [{"code": clerk_code, "long_message": "private provider text"}]})
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado.criar_conta("fixture@example.invalid", "fixture-password")
    assert error.value.account_issue_code == expected_code
    assert error.value.provider_error_code == expected_provider
    assert error.value.remote_effect_possible is True
    if clerk_status == 429:
        assert error.value.retry_after_seconds == 17
    assert "private provider text" not in str(error.value)


def test_signup_chrome_uses_assigned_proxy_tunnel(tmp_path, monkeypatch):
    page = _install_fake_signup_browser(
        tmp_path, monkeypatch, redirect="https://www.crowtado.com/en/dashboard")
    monkeypatch.setattr(registration_proxy, "endpoint", lambda: "http://127.0.0.1:9")

    assert crowtado.criar_conta("fixture@example.invalid", "fixture-password") is None

    command = page.chrome_command[-1]
    assert "--proxy-server=http://127.0.0.1:9" in command
    assert "--disable-quic" in command
    assert all("@" not in part and "151.245.245.253" not in part for part in command)


def test_proxy_manager_does_not_seed_credentials(tmp_path, monkeypatch):
    from moneymin import proxy_manager

    monkeypatch.setattr(proxy_manager, "_manager", None)
    path = tmp_path / "proxies.json"
    assert proxy_manager.get_manager(path).get_proxy_for_account("fixture@example.invalid") is None
    assert not path.exists()
