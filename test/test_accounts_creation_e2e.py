"""Local HTTP registration lifecycle with real storage and isolated providers."""
import json
import threading
import time
from unittest.mock import ANY, Mock

import pytest
from werkzeug.serving import make_server
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from moneymin import config, credential_store, minute_api, token_store
from moneymin.atomic_io import save_json, JsonStateError
from moneymin.web import server, registration_state


EMAIL = "qa@example.invalid"
PASSWORD = "fixture-only-password"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    secrets, data = tmp_path / "secrets", tmp_path / "data"
    for key, value in {"SECRETS_DIR": secrets, "DATA_DIR": data,
                       "HOSTINGER_MAIL_PROFILES": [{"token": "fixture-only", "routes": ["example.invalid"]}]}.items():
        monkeypatch.setattr(config, key, value)
    for key, name in {"PREFS_PATH": "prefs.json", "BALANCES_PATH": "balances.json",
                      "CROWTADO_PW_PATH": "passwords.json", "ACCOUNT_HEALTH_PATH": "health.json"}.items():
        monkeypatch.setattr(server, key, data / name)
    monkeypatch.setattr(server, "_BULK_REGISTER_STATE", {"state": "idle"})
    monkeypatch.setattr(server, "_REGISTRATION_BATCH_RESTORE_BLOCKED", False)
    for runner in ("RUNNER", "RECOVERY", "BALANCES_RUNNER", "ORG_MIGRATION"):
        monkeypatch.setattr(server, runner, Mock(running=False))
    remote = {name: Mock() for name in ("criar_conta", "login", "preencher_demografia", "vincular_minute")}
    for name, method in remote.items():
        monkeypatch.setattr(server.crowtado, name, method)
    token = {"email": EMAIL, "idToken": "fixture-token", "refreshToken": "fixture-refresh", "expires_at": time.time() + 3600}
    register = Mock(side_effect=lambda email, password, code: token_store.save(secrets, email, {**token, "email": email}))
    monkeypatch.setattr(minute_api, "register_identity", register)
    monkeypatch.setattr(minute_api, "login", Mock())
    session = Mock(data=token)
    session.ensure_auth.return_value = {"organizations": [{"resourceKey": config.ORG_KEY, "disabled": False}]}
    monkeypatch.setattr(server.Session, "from_email", Mock(return_value=session))
    monkeypatch.setattr(server.account_bans, "require_not_banned", Mock())
    monkeypatch.setattr(server.time, "sleep", lambda seconds: None)
    body = {"email": EMAIL, "password": PASSWORD, "gender": "female", "birth_month": 2, "birth_year": 1990}
    return server.create_app(for_testing=True), body, remote, register, tmp_path


def await_terminal(client):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        row = client.get("/api/accounts/bulk-register/status").get_json()
        if row["state"] in {"done", "failed"}:
            return row
        threading.Event().wait(0.01)
    pytest.fail("Registration did not terminate")


def test_http_creation_saves_protected_access_and_confirms_all_steps(setup):
    app, body, remote, minute_register, root = setup
    service = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{service.server_port}"
    def call(path, payload=None):
        req = Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=5) as response:
            return json.load(response)
    try:
        assert call("/api/accounts/register?async=1", body)["ok"] is True
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            snapshot = call("/api/accounts/bulk-register/status")
            if snapshot["state"] == "done":
                break
            threading.Event().wait(0.01)
        assert snapshot["created"] == 1 and snapshot["failed"] == 0
        rows = call("/api/accounts")["accounts"]
        assert len(rows) == 1 and rows[0]["registration"]["state"] == "complete"
        assert rows[0]["has_password"] and rows[0]["has_minute_access"]
        assert rows[0]["restriction"]["code"] == "unknown"
        assert call("/api/accounts/password", {"email": EMAIL})["password"] == PASSWORD
        assert credential_store.lookup(config.SECRETS_DIR, EMAIL, strict=True) == PASSWORD
        assert PASSWORD.encode() not in credential_store.record_path(config.SECRETS_DIR, EMAIL).read_bytes()
        for path in config.DATA_DIR.glob("*.json"):
            assert PASSWORD not in path.read_text(encoding="utf-8")
        assert set(snapshot["results"][0]["steps"]) == registration_state.STEPS
        remote["criar_conta"].assert_called_once_with(EMAIL, PASSWORD, on_progress=ANY, on_authenticated=ANY)
        assert callable(remote["criar_conta"].call_args.kwargs["on_progress"])
        minute_register.assert_called_once_with(EMAIL, PASSWORD, config.INVITE_CODE)
        remote["preencher_demografia"].assert_called_once_with(EMAIL, PASSWORD, 2, 1990, "female", session=remote['login'].return_value)
        remote["vincular_minute"].assert_called_once_with(EMAIL, PASSWORD, session=remote['login'].return_value)
        assert rows[0]["registration"]["steps"]["demographics"]["status"] == "ok"
        assert rows[0]["registration"]["steps"]["link_minute"]["status"] == "ok"
    finally:
        service.shutdown(); thread.join(timeout=5)


def test_initial_progress_write_failure_does_not_start_remote_work_or_lock_future_creation(setup, monkeypatch):
    app, body, remote, minute_register, root = setup
    client = app.test_client()
    original = server._save_registration_batch
    monkeypatch.setattr(server, "_save_registration_batch", Mock(side_effect=OSError("disk full")))
    response = client.post("/api/accounts/register?async=1", json=body)
    assert response.status_code == 503
    assert client.get("/api/accounts/bulk-register/status").get_json()["state"] == "failed"
    remote["criar_conta"].assert_not_called()
    minute_register.assert_not_called()
    assert not credential_store.record_path(config.SECRETS_DIR, EMAIL).exists()
    monkeypatch.setattr(server, "_save_registration_batch", original)
    assert client.post("/api/accounts/register?async=1", json=body).status_code == 200
    assert await_terminal(client)["created"] == 1


def test_incomplete_account_visible_password_recoverable_resume_skips_confirmed_creation(setup):
    app, body, remote, minute_register, root = setup
    client = app.test_client()
    rejected = minute_api.AuthError("HTTP 503", code="service")
    rejected.remote_effect_possible = False
    minute_register.side_effect = rejected
    assert client.post("/api/accounts/register?async=1", json=body).status_code == 200
    failed = await_terminal(client)
    assert failed["results"][0]["partial"] is True
    assert server._list_accounts() == []  # incomplete accounts cannot enter campaigns or withdrawals
    row = client.get("/api/accounts").get_json()["accounts"][0]
    assert row["registration"]["state"] == "incomplete"
    assert client.post("/api/accounts/password", json={"email": EMAIL}).get_json()["password"] == PASSWORD
    minute_register.side_effect = lambda email, password, code: token_store.save(config.SECRETS_DIR,email,{"email":email,"idToken":"fixture-token","refreshToken":"fixture-refresh","expires_at":time.time()+3600})
    assert client.post(f"/api/accounts/{EMAIL}/resume", json={}).status_code == 200
    assert await_terminal(client)["created"] == 1
    assert remote["criar_conta"].call_count == 1 and minute_register.call_count == 2
    assert client.post(f"/api/accounts/{EMAIL}/resume", json={}).status_code == 409


def test_overlap_and_stop_finish_current_account_without_starting_another(setup, monkeypatch):
    app, body, remote, minute_register, root = setup
    entered, release = threading.Event(), threading.Event()
    def signup(*args, **kwargs):
        entered.set(); assert release.wait(5)
    remote["criar_conta"].side_effect = signup
    monkeypatch.setattr(server.identity, "gerar_identidade", Mock(return_value={
        **body, "senha": PASSWORD, "nome": "QA", "sobrenome": "Fixture"}))
    client = app.test_client()
    try:
        assert client.post("/api/accounts/bulk-register", json={"count": 2, "domain": "example.invalid", "profiles": [{"birth_month": 2, "birth_year": 1990, "gender": "female"}] * 2}).status_code == 200
        assert entered.wait(3)
        for endpoint in ("/api/accounts/register?async=1", "/api/accounts", "/api/accounts/check-all"):
            assert client.post(endpoint, json=body).status_code == 409
        assert client.put("/api/integrations/hostinger", json={}).status_code == 409
        assert client.post("/api/accounts/bulk-register/stop", json={}).get_json()["state"] == "stopping"
        assert client.post("/api/accounts/bulk-register", json={"count": 1, "domain": "example.invalid"}).status_code == 409
    finally:
        release.set()
    row = await_terminal(client)
    assert row["stopped"] and row["total"] == 2 and row["completed"] == 1
    assert remote["criar_conta"].call_count == 1


@pytest.mark.parametrize("count", [True, "1", 1.2, None, [], -1, 51])
def test_invalid_count_never_starts_remote_creation(setup, count):
    app, body, remote, *_ = setup
    assert app.test_client().post("/api/accounts/bulk-register", json={"count": count, "domain": "example.invalid"}).status_code == 400
    remote["criar_conta"].assert_not_called()


@pytest.mark.parametrize("patch", [{"email": "invalid"}, {"birth_month": True}, {"birth_year": "1990"}, {"gender": "invalid"}, {"password": []}])
def test_invalid_identity_never_starts_remote_creation(setup, patch):
    app, body, remote, *_ = setup
    assert app.test_client().post("/api/accounts/register?async=1", json={**body, **patch}).status_code == 400
    remote["criar_conta"].assert_not_called()


def test_existing_password_is_preserved_before_remote_work(setup):
    app, body, remote, *_ = setup
    credential_store.save(config.SECRETS_DIR, EMAIL, "previous-fixture-password")
    client = app.test_client()
    assert client.post("/api/accounts/register?async=1", json=body).status_code == 200
    assert await_terminal(client)["created"] == 0
    assert credential_store.lookup(config.SECRETS_DIR, EMAIL) == "previous-fixture-password"
    remote["criar_conta"].assert_not_called()


def test_restart_does_not_repeat_an_uncertain_creation(setup):
    app, *_ = setup
    save_json(config.DATA_DIR / "account_registration_batch.json", {
        "state": "running", "total": 1, "completed": 0, "created": 0, "failed": 0, "results": []})
    server._restore_registration_batch()
    assert server._BULK_REGISTER_STATE["state"] == "failed"
    assert "retome" in server._BULK_REGISTER_STATE["error"]


def test_registration_request_replay_is_idempotent_even_after_service_restart(setup):
    app, body, remote, *_ = setup
    client = app.test_client()
    body = {**body, "request_id": "fixture-request-unique-id"}
    assert client.post("/api/accounts/register?async=1", json=body).status_code == 200
    assert await_terminal(client)["created"] == 1
    for _ in range(3):
        reply = client.post("/api/accounts/register?async=1", json=body)
        assert reply.status_code == 200 and reply.get_json()["existing"]
    server._restore_registration_batch()
    assert client.post("/api/accounts/register?async=1", json=body).get_json()["existing"]
    assert client.post("/api/accounts/register?async=1", json={**body, "birth_month": 3}).status_code == 409
    assert remote["criar_conta"].call_count == 1


def test_bad_journal_preserved_and_blocks_creation(setup):
    app, body, remote, *_ = setup
    path = config.DATA_DIR / "account_registrations.json"
    save_json(path, {EMAIL: {"email": EMAIL, "state": "incomplete", "steps": {}, "identity": {"password": "should-not-exist"}}})
    before = path.read_bytes()
    with pytest.raises(JsonStateError):
        registration_state.load()
    assert path.read_bytes() == before
    remote["criar_conta"].assert_not_called()


@pytest.mark.parametrize("code", ["user_banned", "user_account_disabled", "user_disabled"])
def test_explicit_clerk_restriction_is_archived_and_prevents_minute_creation_on_resume(setup, code):
    app, body, remote, minute_register, *_ = setup
    error = server.crowtado._remote_error("Login Crowtado", 403, {"errors": [{"code": code, "long_message": "sensitive-remote-detail"}]})
    assert error.account_issue_code == "restricted"
    assert "sensitive" not in str(error)
    remote["login"].side_effect = error
    client = app.test_client()
    client.post("/api/accounts/register?async=1", json=body)
    assert await_terminal(client)["failed"] == 1
    assert client.get("/api/accounts").get_json()["accounts"] == []
    archived = client.get("/api/accounts/banned").get_json()["accounts"]
    assert len(archived) == 1 and archived[0]["email"] == EMAIL
    assert archived[0]["restriction_confirmed"] is True
    remote["login"].side_effect = error
    assert client.post(f"/api/accounts/{EMAIL}/resume", json={}).status_code == 404
    minute_register.assert_not_called()
    assert remote["criar_conta"].call_count == 1


def test_plain_403_does_not_assert_account_banned(setup):
    app, body, remote, *_ = setup
    error = server.crowtado._remote_error("Login Crowtado", 403, {"errors": [{"code": "generic_forbidden"}]})
    assert error.account_issue_code == "forbidden"


@pytest.mark.parametrize("blocked_step", ["crowtado", "minute"])
def test_confirmed_registration_ban_stops_batch_and_preserves_evidence(setup, monkeypatch, blocked_step):
    app, body, remote, minute_register, _ = setup
    monkeypatch.setattr(server.identity, "gerar_identidade", Mock(return_value={
        "email": EMAIL, "senha": PASSWORD, "nome": "QA", "sobrenome": "Fixture"}))
    if blocked_step == "crowtado":
        remote["login"].side_effect = server.crowtado._remote_error(
            "Login", 403, {"errors": [{"code": "user_banned"}]})
    else:
        minute_register.side_effect = minute_api.AuthError(
            "Fictional ban", code="restricted", provider_error_code="user_account_disabled")
    client = app.test_client()
    assert client.post("/api/accounts/bulk-register", json={"count": 3, "domain": "example.invalid", "profiles": [{"birth_month": 2, "birth_year": 1990, "gender": "female"}] * 3}).status_code == 200
    result = await_terminal(client)
    assert result["completed"] == 1 and result["failed"] == 1 and result["created"] == 0
    assert result["stopped"] is True and "restrição" in result["stop_reason"]
    assert server.identity.gerar_identidade.call_count == 1
    remote["criar_conta"].assert_called_once()
    assert client.get("/api/accounts").json["accounts"] == []
    archived = client.get("/api/accounts/banned/monitor").json["accounts"][0]
    assert archived["diagnostic"]["provider"] == blocked_step
    assert archived["diagnostic"]["code"] == "restricted"
    assert archived["diagnostic"]["steps"]["crowtado_signup"] == "ok"
    assert archived["diagnostic"]["registration_step"] == ("ban_check" if blocked_step == "crowtado" else "minute_identity")
    assert PASSWORD not in json.dumps(archived)
    persisted = json.loads((config.DATA_DIR / "account_registration_batch.json").read_text(encoding="utf-8"))
    assert persisted["results"][0]["removed"] is True
    assert persisted["results"][0]["removable"] is False
    assert persisted["results"][0]["steps"] == result["results"][0]["steps"]
    assert not credential_store.record_path(config.SECRETS_DIR, EMAIL).exists()
    assert client.post(f"/api/accounts/{EMAIL}/resume", json={}).status_code == 404
    server._restore_registration_batch()
    assert client.get("/api/accounts/bulk-register/status").json["results"][0]["removed"] is True


@pytest.mark.parametrize("code", ["forbidden", "network", "timeout"])
def test_uncertain_failure_never_triggers_batch_ban_stop(setup, code):
    app, body, remote, _, _ = setup
    remote["login"].side_effect = server.crowtado.CrowtadoError("Fictional uncertainty", code=code)
    client = app.test_client()
    client.post("/api/accounts/register?async=1", json=body)
    result = await_terminal(client)
    assert not result["stopped"] and "stop_reason" not in result
    client.get("/api/accounts")
    assert client.get("/api/accounts/banned/monitor").json["accounts"] == []


def test_no_referral_registration_passes_empty_ref_and_persists_the_choice(setup):
    app, body, remote, *_ = setup
    client=app.test_client()
    assert client.post("/api/accounts/register?async=1", json={**body,"use_referral":False}).status_code==200
    assert await_terminal(client)["created"]==1
    remote["criar_conta"].assert_called_once_with(EMAIL,PASSWORD,ref="",on_progress=ANY, on_authenticated=ANY)
    assert registration_state.load()[EMAIL]["identity"]["use_referral"] is False


def test_wrong_password_attempt_does_not_hide_previously_complete_account(setup):
    app,body,remote,*_=setup
    client=app.test_client()
    client.post("/api/accounts/register?async=1",json=body)
    assert await_terminal(client)["created"]==1
    client.post("/api/accounts/register?async=1",json={**body,"password":"different-fixture-password"})
    assert await_terminal(client)["failed"]==1
    assert registration_state.load()[EMAIL]["state"]=="complete"
    assert len(server._list_accounts())==1
    assert credential_store.lookup(config.SECRETS_DIR,EMAIL)==PASSWORD
    remote["criar_conta"].assert_called_once()


def test_resume_own_signup_retries_minute_without_repeating_crowtado_or_site_steps(setup):
    app, body, remote, minute_register, root = setup
    client = app.test_client()
    save_token = minute_register.side_effect
    rejected = minute_api.AuthError("temporary failure", code="service")
    rejected.remote_effect_possible = False
    minute_register.side_effect = rejected
    client.post("/api/accounts/register?async=1", json=body)
    assert await_terminal(client)["failed"] == 1
    minute_register.side_effect = save_token
    client.post(f"/api/accounts/{EMAIL}/resume", json={})
    assert await_terminal(client)["created"] == 1
    for name in ("preencher_demografia", "vincular_minute"):
        remote[name].assert_called_once()
    remote["criar_conta"].assert_called_once()
    assert minute_register.call_count == 2


@pytest.mark.parametrize("message", ["DEMOGRAPHICS_REQUIRED", "Birth month, birth year, and gender are required"])
def test_explicit_trpc_demographics_gate_remains_actionable_without_remote_message_leak(message):
    error = server.crowtado._trpc_error("externalMobileCapture.saveEmail", 412,
        [{"error": {"json": {"message": message, "data": {"code": "PRECONDITION_FAILED"}}}}])
    assert error.account_issue_code == "demographics_required"
    assert "DEMOGRAPHICS_REQUIRED" in str(error)


def test_generic_412_does_not_authorize_demographic_changes():
    error = server.crowtado._trpc_error("externalMobileCapture.saveEmail", 412,
        {"error": {"json": {"message": "Some other precondition"}}})
    assert error.account_issue_code != "demographics_required"


def test_unavailable_site_task_preserves_account_but_does_not_claim_completion(setup):
    app, body, remote, minute_register, root = setup
    error = server.crowtado._trpc_error("externalMobileCapture.saveEmail", 412,
        [{"error": {"json": {"message": "minute_task_unavailable"}}}])
    assert error.account_issue_code == "minute_task_unavailable"
    remote["vincular_minute"].side_effect = error
    client = app.test_client()
    client.post("/api/accounts/register?async=1", json=body)
    assert await_terminal(client)["failed"] == 1
    row = client.get("/api/accounts").get_json()["accounts"][0]
    assert row["registration"]["state"] == "incomplete" and row["has_password"]
    assert row["registration"]["steps"]["link_minute"]["code"] == "minute_task_unavailable"
    remote["vincular_minute"].assert_called_once()
    remote["preencher_demografia"].assert_called_once()
    assert server._list_accounts() == []


def test_missing_profile_data_does_not_claim_full_registration(setup):
    app, body, remote, *_ = setup
    client = app.test_client()
    assert client.post("/api/accounts/register?async=1",json={"email":EMAIL,"password":PASSWORD}).status_code == 200
    assert await_terminal(client)["failed"] == 1
    assert registration_state.load()[EMAIL]["steps"]["demographics"]["code"] == "demographics_required"
    remote["preencher_demografia"].assert_not_called()
    remote["vincular_minute"].assert_not_called()


def test_proxy_import_endpoint_is_private_transactional_and_locked_during_creation(setup):
    app, body, remote, *_ = setup
    client = app.test_client()
    text = "127.0.0.1:8080:fixture-user:fixture-password"
    response = client.post("/api/accounts/proxies/import",json={"text":text})
    assert response.status_code == 200 and response.get_json()["total"] == 1
    assert "fixture-user" not in response.get_data(as_text=True)
    assert "fixture-password" not in response.get_data(as_text=True)
    rows = client.get("/api/accounts/proxies").get_json()
    assert rows["proxies"][0]["label"] == "127.0.0.1:8080"
    assert client.post("/api/accounts/proxies/import",json={"text":text+"\ninvalid"}).status_code == 400
    assert client.get("/api/accounts/proxies").get_json() == rows
    server._BULK_REGISTER_STATE["state"] = "running"
    assert client.post("/api/accounts/proxies/import",json={"text":text}).status_code == 409


def test_crowtado_and_minute_use_same_proxy_and_resume_reuses_it(setup, monkeypatch):
    from moneymin import registration_proxy as proxies
    app, body, remote, register, *_ = setup
    client = app.test_client()
    client.post("/api/accounts/proxies/import",json={"text":"127.0.0.1:8080:fixture-user:fixture-password\n127.0.0.2:8081:other-user:other-password"})
    observed = []
    monkeypatch.setattr(proxies,"check_exit_ip",lambda: "203.0.113.1")
    remote["criar_conta"].side_effect = lambda *a,**k: observed.append(proxies.endpoint())
    save_token = register.side_effect
    def fail_once(*args):
        observed.append(proxies.endpoint())
        raise RuntimeError("fixture-user fixture-password temporary failure")
    register.side_effect = fail_once
    client.post("/api/accounts/register?async=1",json={**body,"proxy_id":"auto"})
    failed = await_terminal(client)
    assert failed["failed"] == 1 and "fixture-password" not in json.dumps(failed)
    assert "fixture-user" not in json.dumps(failed)
    assert observed[0] == observed[1] and observed[0].startswith("http://127.0.0.1:")
    first = registration_state.load()[EMAIL]["identity"]["proxy_id"]
    register.side_effect = save_token
    client.post(f"/api/accounts/{EMAIL}/resume",json={})
    assert await_terminal(client)["created"] == 1
    assert registration_state.load()[EMAIL]["identity"]["proxy_id"] == first
    remote["criar_conta"].assert_called_once()
    assert proxies.endpoint() is None


def test_proxy_failure_prevents_both_remote_creations_without_direct_retry(setup, monkeypatch):
    from moneymin import registration_proxy as proxies
    app, body, remote, register, *_ = setup
    client = app.test_client()
    client.post("/api/accounts/proxies/import",json={"text":"127.0.0.1:8080:fixture-user:fixture-password"})
    monkeypatch.setattr(proxies,"check_exit_ip",Mock(side_effect=RuntimeError("fixture-password upstream rejected")))
    client.post("/api/accounts/register?async=1",json={**body,"proxy_id":"auto"})
    row = await_terminal(client)
    assert row["failed"] == 1 and row["results"][0]["steps"]["proxy"]["status"] == "fail"
    assert "fixture-password" not in json.dumps(row)
    remote["criar_conta"].assert_not_called(); register.assert_not_called()
    assert proxies.endpoint() is None


def test_invalid_proxy_selection_is_rejected_before_starting_worker(setup):
    app, body, remote, register, *_ = setup
    response = app.test_client().post("/api/accounts/register?async=1",json={**body,"proxy_id":"auto"})
    assert response.status_code == 400
    remote["criar_conta"].assert_not_called(); register.assert_not_called()


def test_resume_legacy_link_failure_completes_link_and_preserves_both_accounts(setup):
    app, body, remote, register, *_ = setup
    client = app.test_client()
    client.post("/api/accounts/register?async=1",json=body)
    assert await_terminal(client)["created"] == 1
    row = registration_state.load()[EMAIL]
    row["steps"]["link_minute"] = {"status":"fail","detail":"task unavailable","code":"minute_task_unavailable"}
    registration_state.update(EMAIL,steps=row["steps"],state="incomplete",error="old site step failed")
    client.post(f"/api/accounts/{EMAIL}/resume",json={})
    assert await_terminal(client)["created"] == 1
    assert registration_state.load()[EMAIL]["steps"]["link_minute"]["status"] == "ok"
    remote["criar_conta"].assert_called_once(); register.assert_called_once()
    remote["preencher_demografia"].assert_called_once()
    assert remote["vincular_minute"].call_count == 2


@pytest.mark.parametrize("code", ["restricted", "network", "timeout", "forbidden", "invalid_response"])
def test_post_creation_access_failure_is_never_counted_as_success(setup, code):
    app, body, remote, register, *_ = setup
    client = app.test_client()
    remote["login"].side_effect = server.crowtado.CrowtadoError(
        "fixture account blocked" if code=="restricted" else "fixture consultation inconclusive",
        code=code, provider_error_code="user_banned" if code=="restricted" else None)
    client.post("/api/accounts/register?async=1",json=body)
    row = await_terminal(client)
    assert row["created"] == 0 and row["failed"] == 1
    result = row["results"][0]
    assert result["created"] is False and result["partial"] is True
    assert result["steps"]["ban_check"]["status"] == "fail" and result["steps"]["ban_check"]["code"] == code
    accounts = client.get("/api/accounts").get_json()["accounts"]
    if code == "restricted":
        assert accounts == []
        assert server.account_bans.banned_emails() == {EMAIL}
    else:
        assert accounts[0]["registration"]["state"] == "incomplete"
        assert accounts[0]["restriction"]["confirmed"] is False
        assert server.account_bans.banned_emails() == set()
    assert server._list_accounts() == []
    register.assert_not_called()
    remote["criar_conta"].assert_called_once()


def test_uncertain_access_can_be_rechecked_without_recreating_the_account(setup):
    app, body, remote, register, *_ = setup
    client = app.test_client()
    remote["login"].side_effect = server.crowtado.CrowtadoError("temporary timeout",code="timeout")
    client.post("/api/accounts/register?async=1",json=body)
    assert await_terminal(client)["created"] == 0
    remote["login"].side_effect = None
    client.post(f"/api/accounts/{EMAIL}/resume",json={})
    assert await_terminal(client)["created"] == 1
    remote["criar_conta"].assert_called_once()
    register.assert_called_once()
    assert remote["login"].call_count == 2


def test_same_account_new_request_keeps_confirmed_remote_creation(setup):
    app, body, remote, register, *_ = setup
    client = app.test_client()
    client.post("/api/accounts/register?async=1",json=body)
    assert await_terminal(client)["created"] == 1
    client.post("/api/accounts/register?async=1",json=body)
    assert await_terminal(client)["created"] == 1
    remote["criar_conta"].assert_called_once(); register.assert_called_once()


def test_minute_suspended_org_is_not_counted_as_success_or_archived_as_ban(setup):
    app, body, remote, register, *_ = setup
    session = server.Session.from_email.return_value
    session.ensure_auth.return_value["organizations"][0]["disabled"] = True
    client = app.test_client()
    client.post("/api/accounts/register?async=1",json=body)
    result = await_terminal(client)
    assert result["created"] == 0 and result["failed"] == 1
    assert result["results"][0]["steps"]["minute_register"]["code"] == "access_paused"
    register.assert_called_once()
    assert server._list_accounts() == []
    listed = client.get("/api/accounts").get_json()["accounts"]
    assert all(row["email"] != EMAIL or row["restriction"]["confirmed"] is False for row in listed)
    assert client.get("/api/accounts/banned").get_json()["accounts"] == []
    assert server.account_bans.banned_emails() == set()


def test_minute_target_org_auth_block_is_not_counted_as_success(setup):
    app, body, remote, register, *_ = setup
    session = server.Session.from_email.return_value
    profile = session.ensure_auth.return_value
    def auth(**kwargs):
        if kwargs.get("org_key") == config.ORG_KEY:
            raise minute_api.AuthError("Fixture organization access paused",code="access_paused")
        return profile
    session.ensure_auth.side_effect = auth
    client = app.test_client()
    client.post("/api/accounts/register?async=1",json=body)
    result = await_terminal(client)
    assert result["created"] == 0 and result["results"][0]["steps"]["minute_register"]["code"] == "access_paused"
    assert server._list_accounts() == []
    session.ensure_auth.assert_called_with(org_key=config.ORG_KEY)
