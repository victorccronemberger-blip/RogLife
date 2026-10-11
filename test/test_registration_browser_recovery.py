"""Bounded interactive signup, cancellation and durable recovery; no real accounts."""
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from moneymin import config, credential_store, crowtado, hostinger_mail
from moneymin.web import registration_state, server
from test_crowtado_account_validation import _install_fake_signup_browser
from test_accounts_creation_e2e import setup, await_terminal, EMAIL, PASSWORD


SUCCESS = "https://www.crowtado.com/pt-BR/dashboard"


def test_ban_during_session_activation_is_not_hidden_by_browser_timeout(tmp_path, monkeypatch):
    page = _install_fake_signup_browser(tmp_path, monkeypatch, redirect=SUCCESS)
    def session_unavailable(current_page, _email):
        assert current_page is page
        page.response_handler(SimpleNamespace(
            url=crowtado.CLERK_BASE + '/v1/client', status=403, headers={},
            json=lambda: {'errors': [{'code': 'user_banned', 'long_message': 'private provider text'}]}))
        raise TimeoutError('private browser details')
    monkeypatch.setattr(crowtado, '_registration_session_from_page', session_unavailable)
    continuation = Mock()
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado.criar_conta(EMAIL, PASSWORD, on_authenticated=continuation)
    assert error.value.account_issue_code == 'restricted'
    assert error.value.http_status == 403
    assert error.value.provider_error_code == 'user_banned'
    assert error.value.phase == 'signup_confirmation'
    assert 'private' not in str(error.value)
    continuation.assert_not_called()
    assert page.submit_attempts == 1
    assert page.process.terminated


def test_provider_captcha_rejection_stops_without_repeating_submit(tmp_path, monkeypatch):
    page = _install_fake_signup_browser(
        tmp_path, monkeypatch, response_timing="clerk_then_proof", clerk_status=400,
        clerk_payload={"errors": [{"code": "captcha_invalid", "long_message": "private provider data"}]})
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado.criar_conta(EMAIL, PASSWORD)
    assert error.value.account_issue_code == "captcha_required"
    assert error.value.http_status == 400
    assert error.value.remote_effect_possible is True
    assert error.value.phase == "signup_submission"
    assert "private provider data" not in str(error.value)
    assert page.submit_attempts == 1
    assert page.process.terminated


@pytest.mark.parametrize("failure", ["consent_error", "consent_missed"])
def test_unconfirmed_terms_stop_before_filling_or_submitting(tmp_path, monkeypatch, failure):
    page = _install_fake_signup_browser(tmp_path, monkeypatch, redirect=SUCCESS)
    setattr(page, failure, True)
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado.criar_conta(EMAIL, PASSWORD)
    assert error.value.account_issue_code == "signup_not_ready"
    assert error.value.remote_effect_possible is False
    assert not page.did_fill
    assert page.submit_attempts == 0


@pytest.mark.parametrize("after_submit", [False, True])
def test_captcha_waits_for_user_then_confirms_without_an_extra_submit(tmp_path, monkeypatch, after_submit):
    page = _install_fake_signup_browser(tmp_path, monkeypatch,
                                        redirect=None if after_submit else SUCCESS)
    if after_submit:
        page.on_submit = lambda: setattr(page, "captcha_pending", True)
    else:
        page.captcha_pending = True
    events = []

    def finish_challenge():
        if page.front_count:
            assert page.submit_attempts == int(after_submit)
            page.captcha_pending = False
            if after_submit:
                page.url = SUCCESS

    page.on_wait = finish_challenge
    crowtado.criar_conta(EMAIL, PASSWORD, on_progress=events.append)
    assert "captcha" in events
    assert page.submit_attempts == 1
    assert page.front_count == 1
    assert page.process.terminated


@pytest.mark.parametrize("after_submit", [False, True])
def test_captcha_timeout_is_bounded_and_preserves_submission_uncertainty(tmp_path, monkeypatch, after_submit):
    page = _install_fake_signup_browser(tmp_path, monkeypatch)
    if after_submit:
        page.on_submit = lambda: setattr(page, "captcha_pending", True)
    else:
        page.captcha_pending = True
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado.criar_conta(EMAIL, PASSWORD)
    assert error.value.account_issue_code == "captcha_required"
    assert error.value.remote_effect_possible is after_submit
    assert page.submit_attempts == int(after_submit)
    assert 300 <= crowtado.time.monotonic() < 310
    assert page.process.terminated


def test_user_submission_while_waiting_is_observed_without_automatic_resubmit(tmp_path, monkeypatch):
    page = _install_fake_signup_browser(tmp_path, monkeypatch)
    page.captcha_pending = True

    def user_submits():
        if page.front_count:
            page.request_handler(SimpleNamespace(method="POST", url=crowtado.CLERK_BASE + "/v1/client/sign_ups"))
            page.url = SUCCESS

    page.on_wait = user_submits
    crowtado.criar_conta(EMAIL, PASSWORD)
    assert page.submit_attempts == 0


def test_fields_changed_during_manual_verification_are_not_submitted(tmp_path, monkeypatch):
    page = _install_fake_signup_browser(tmp_path, monkeypatch)
    page.captcha_pending = True

    def edit_field():
        if page.front_count:
            page.fields["#emailAddress-field"] = "changed@example.invalid"
            page.captcha_pending = False

    page.on_wait = edit_field
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado.criar_conta(EMAIL, PASSWORD)
    assert error.value.account_issue_code == "signup_field_unconfirmed"
    assert error.value.remote_effect_possible is False
    assert page.submit_attempts == 0


@pytest.mark.parametrize("after_submit", [False, True])
def test_browser_closed_during_challenge_preserves_effect_evidence(tmp_path, monkeypatch, after_submit):
    page = _install_fake_signup_browser(tmp_path, monkeypatch)
    if after_submit:
        page.on_submit = lambda: setattr(page, "captcha_pending", True)
    else:
        page.captcha_pending = True
    page.on_wait = lambda: setattr(page, "closed", bool(page.front_count))
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado.criar_conta(EMAIL, PASSWORD)
    assert error.value.account_issue_code == "signup_browser_closed"
    assert error.value.remote_effect_possible is after_submit
    assert page.process.terminated


def test_stop_during_captcha_closes_browser_without_submitting(tmp_path, monkeypatch):
    page = _install_fake_signup_browser(tmp_path, monkeypatch)
    page.captcha_pending = True

    def stop(event):
        assert event == "captcha"
        raise crowtado.CrowtadoError("stopped", code="registration_cancelled")

    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado.criar_conta(EMAIL, PASSWORD, on_progress=stop)
    assert error.value.account_issue_code == "registration_cancelled"
    assert error.value.remote_effect_possible is False
    assert page.submit_attempts == 0
    assert page.process.terminated


def test_progress_storage_failure_aborts_before_click_without_losing_error_type(tmp_path, monkeypatch):
    page = _install_fake_signup_browser(tmp_path, monkeypatch, redirect=SUCCESS)
    with pytest.raises(OSError, match="checkpoint unavailable"):
        crowtado.criar_conta(EMAIL, PASSWORD, on_progress=Mock(side_effect=OSError("checkpoint unavailable")))
    assert page.submit_attempts == 0
    assert page.process.terminated


def test_confirmation_timeout_never_repeats_the_signup(tmp_path, monkeypatch):
    page = _install_fake_signup_browser(tmp_path, monkeypatch)
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado.criar_conta(EMAIL, PASSWORD)
    assert error.value.account_issue_code == "signup_unconfirmed"
    assert error.value.remote_effect_possible is True
    assert page.submit_attempts == 1
    assert 60 <= crowtado.time.monotonic() < 70


def test_email_code_requires_actual_post_verification_redirect(tmp_path, monkeypatch):
    page = _install_fake_signup_browser(tmp_path, monkeypatch,
                                        redirect="https://www.crowtado.com/pt-BR/sign-up/verify-email-address")
    page.otp_redirect = SUCCESS
    code = Mock(return_value="123456")
    monkeypatch.setattr(hostinger_mail, "wait_for_code", code)
    crowtado.criar_conta(EMAIL, PASSWORD)
    code.assert_called_once_with(EMAIL, sender="crowtado.com", min_uid=0, timeout=180)
    assert page.submit_attempts == 1 and page.otp_submits == 1


SESSION_TRANSFER = "https://accounts.crowtado.com/sign-in?redirect_url=https%3A%2F%2Fwww.crowtado.com%2Fpt-BR%2Fdashboard"


def test_email_verification_waits_for_existing_session_handoff_without_another_login(tmp_path, monkeypatch):
    page = _install_fake_signup_browser(tmp_path, monkeypatch,
                                        redirect=crowtado.SITE_BASE + '/pt-BR/sign-up/verify-email-address')
    page.otp_redirect = SESSION_TRANSFER
    monkeypatch.setattr(hostinger_mail, 'wait_for_code', lambda *_args, **_kwargs: '123456')
    session = object()
    extract = Mock(return_value=session)
    monkeypatch.setattr(crowtado, '_registration_session_from_page', extract)
    continuation = Mock()

    def finish_handoff():
        if page.url == SESSION_TRANSFER:
            page.url = crowtado.SITE_BASE + '/pt-BR/complete-profile?next=%2Fpt-BR%2Fdashboard'

    page.on_wait = finish_handoff
    crowtado.criar_conta(EMAIL, PASSWORD, on_authenticated=continuation)
    extract.assert_called_once_with(page, EMAIL)
    continuation.assert_called_once_with(session)
    assert page.submit_attempts == 1 and page.otp_submits == 1
    assert set(page.fields) == {'#emailAddress-field', '#password-field'}


def test_persistent_return_to_accounts_signin_is_not_success_or_a_second_signup(tmp_path, monkeypatch):
    page = _install_fake_signup_browser(tmp_path, monkeypatch,
                                        redirect=crowtado.SITE_BASE + '/pt-BR/sign-up/verify-email-address')
    page.otp_redirect = SESSION_TRANSFER
    monkeypatch.setattr(hostinger_mail, 'wait_for_code', lambda *_args, **_kwargs: '123456')
    continuation = Mock()
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado.criar_conta(EMAIL, PASSWORD, on_authenticated=continuation)
    assert error.value.account_issue_code == 'signup_session_unconfirmed'
    assert error.value.phase == 'signup_confirmation'
    assert error.value.remote_effect_possible is True
    assert page.submit_attempts == 1 and page.otp_submits == 1
    continuation.assert_not_called()


@pytest.mark.parametrize('url', [
    SESSION_TRANSFER.replace('accounts.crowtado.com', 'accounts.crowtado.com.evil.invalid'),
    SESSION_TRANSFER.replace('https://accounts.', 'http://accounts.'),
    SESSION_TRANSFER.replace('www.crowtado.com', 'evil.invalid'),
    SESSION_TRANSFER + '&redirect_url=https%3A%2F%2Fevil.invalid',
])
def test_session_handoff_requires_official_origin_and_one_official_return_url(url):
    assert not crowtado._is_signup_session_transfer_url(url)


def test_email_delivery_error_keeps_safe_diagnostic_and_never_retries_signup(tmp_path, monkeypatch):
    page = _install_fake_signup_browser(tmp_path, monkeypatch,
                                        redirect="https://www.crowtado.com/pt-BR/sign-up/verify-email-address")
    monkeypatch.setattr(hostinger_mail, "wait_for_code", Mock(side_effect=RuntimeError("private mailbox credential")))
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado.criar_conta(EMAIL, PASSWORD)
    assert error.value.account_issue_code == "signup_email_unconfirmed"
    assert error.value.remote_effect_possible is True
    assert "private" not in str(error.value)
    assert page.submit_attempts == 1 and page.otp_submits == 0


def test_entered_otp_without_confirmation_is_not_a_success(tmp_path, monkeypatch):
    page = _install_fake_signup_browser(tmp_path, monkeypatch,
                                        redirect="https://www.crowtado.com/pt-BR/sign-up/verify-email-address")
    monkeypatch.setattr(hostinger_mail, "wait_for_code", lambda *_args, **_kwargs: "123456")
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado.criar_conta(EMAIL, PASSWORD)
    assert error.value.account_issue_code == "signup_unconfirmed"
    assert error.value.phase == "signup_confirmation"
    assert error.value.remote_effect_possible is True
    assert page.otp_submits == 1


@pytest.mark.parametrize("value", [None, "12345", "1234567", "ABCDEF"])
def test_malformed_email_code_is_not_submitted(tmp_path, monkeypatch, value):
    page = _install_fake_signup_browser(tmp_path, monkeypatch,
                                        redirect="https://www.crowtado.com/pt-BR/sign-up/verify-email-address")
    monkeypatch.setattr(hostinger_mail, "wait_for_code", lambda *_args, **_kwargs: value)
    with pytest.raises(crowtado.CrowtadoError) as error:
        crowtado.criar_conta(EMAIL, PASSWORD)
    assert error.value.account_issue_code == "signup_email_unconfirmed"
    assert error.value.phase == "email_verification"
    assert error.value.remote_effect_possible is True
    assert page.submit_attempts == 1


def test_http_captcha_progress_and_stop_preserve_password_and_stop_batch(setup, monkeypatch):
    app, body, remote, register, _ = setup
    entered, release = threading.Event(), threading.Event()

    def waiting_signup(*args, on_progress, **kwargs):
        on_progress("captcha")
        entered.set()
        assert release.wait(5)
        on_progress("captcha")

    remote["criar_conta"].side_effect = waiting_signup
    generate = Mock(return_value={"email": EMAIL, "senha": PASSWORD, "nome": "QA", "sobrenome": "Fixture"})
    monkeypatch.setattr(server.identity, "gerar_identidade", generate)
    client = app.test_client()
    try:
        assert client.post("/api/accounts/bulk-register", json={"count": 2, "domain": "example.invalid", "profiles": [{"birth_month": 5, "birth_year": 1999, "gender": "male"}] * 2}).status_code == 200
        assert entered.wait(3)
        snapshot = client.get("/api/accounts/bulk-register/status").get_json()
        assert "Aguardando CAPTCHA" in snapshot["current_step"]
        saved = registration_state.load()[EMAIL]["steps"]["crowtado_signup"]
        assert saved["status"] == "manual" and saved["remote_effect_possible"] is True
        assert client.post("/api/accounts/bulk-register/stop", json={}).status_code == 200
    finally:
        release.set()
    snapshot = await_terminal(client)
    assert snapshot["stopped"] is True
    assert snapshot["created"] == 0
    assert snapshot["results"][0]["steps"]["crowtado_signup"]["code"] == "registration_cancelled"
    assert credential_store.lookup(config.SECRETS_DIR, EMAIL, strict=True) == PASSWORD
    generate.assert_called_once()
    remote["criar_conta"].assert_called_once()
    register.assert_not_called()


@pytest.mark.parametrize("code", ["captcha_required", "signup_not_ready", "signup_browser_closed", "signup_field_unconfirmed"])
def test_action_required_does_not_retry_or_start_next_identity(setup, monkeypatch, code):
    app, _, remote, register, _ = setup
    remote["criar_conta"].side_effect = crowtado.CrowtadoError("User action required", code=code,
                                                               phase="signup_submission", remote_effect_possible=False)
    generate = Mock(return_value={"email": EMAIL, "senha": PASSWORD, "nome": "QA", "sobrenome": "Fixture"})
    monkeypatch.setattr(server.identity, "gerar_identidade", generate)
    client = app.test_client()
    assert client.post("/api/accounts/bulk-register", json={"count": 2, "domain": "example.invalid", "profiles": [{"birth_month": 5, "birth_year": 1999, "gender": "male"}] * 2}).status_code == 200
    snapshot = await_terminal(client)
    assert snapshot["state"] == "failed" and snapshot["completed"] == 1
    assert registration_state.load()[EMAIL]["steps"]["crowtado_signup"]["remote_effect_possible"] is False
    generate.assert_called_once()
    remote["criar_conta"].assert_called_once()
    register.assert_not_called()


def test_exhausted_safe_retries_keep_phase_code_and_negative_effect_evidence(setup):
    app, body, remote, register, _ = setup
    remote["criar_conta"].side_effect = crowtado.CrowtadoError("setup timeout", code="timeout",
                                                               phase="browser_setup", remote_effect_possible=False)
    client = app.test_client()
    assert client.post("/api/accounts/register?async=1", json=body).status_code == 200
    await_terminal(client)
    step = registration_state.load()[EMAIL]["steps"]["crowtado_signup"]
    assert step["code"] == "timeout" and step["phase"] == "browser_setup"
    assert step["remote_effect_possible"] is False
    assert remote["criar_conta"].call_count == server._CROWTADO_SIGNUP_RETRIES
    register.assert_not_called()


def test_interrupted_manual_checkpoint_resumes_by_login_without_new_signup(setup):
    app, body, remote, register, _ = setup

    def interrupted(*args, on_progress, **kwargs):
        on_progress("captcha")
        raise OSError("fixture interruption after durable manual checkpoint")

    remote["criar_conta"].side_effect = interrupted
    client = app.test_client()
    assert client.post("/api/accounts/register?async=1", json=body).status_code == 200
    assert await_terminal(client)["state"] == "failed"
    step = registration_state.load()[EMAIL]["steps"]["crowtado_signup"]
    assert step["status"] == "manual" and step["remote_effect_possible"] is True
    remote["criar_conta"].side_effect = None
    assert client.post(f"/api/accounts/{EMAIL}/resume", json={}).status_code == 200
    assert await_terminal(client)["created"] == 1
    remote["criar_conta"].assert_called_once()
    remote["login"].assert_called_once()
    register.assert_called_once()


def test_confirmed_pre_submit_captcha_timeout_can_resume_same_account(setup):
    app, body, remote, register, _ = setup
    remote["criar_conta"].side_effect = crowtado.CrowtadoError(
        "captcha expired before submit", code="captcha_required",
        phase="signup_submission", remote_effect_possible=False)
    client = app.test_client()
    assert client.post("/api/accounts/register?async=1", json=body).status_code == 200
    assert await_terminal(client)["state"] == "failed"
    register.assert_not_called()
    remote["criar_conta"].side_effect = None
    assert client.post(f"/api/accounts/{EMAIL}/resume", json={}).status_code == 200
    assert await_terminal(client)["created"] == 1
    assert remote["criar_conta"].call_count == 2
    assert remote["criar_conta"].call_args.args == (EMAIL, PASSWORD)
