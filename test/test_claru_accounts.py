"""Two identities and two independent secrets, with no Crowtado operations."""
import json
from unittest.mock import Mock
import pytest

from moneymin import claru, config, credential_store, minute_api
from moneymin.web import server, registration_state
from test_accounts_creation_e2e import setup, await_terminal

LOGIN = "person@example.invalid"
MINUTE = "generated@supply.claru.ai"
CLARU_PASSWORD = "fixture-claru-secret"
MINUTE_PASSWORD = "fixture-minute-secret"


@pytest.fixture
def remote(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path / "secrets")
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(claru.requests, "Session", Mock(return_value=session))
    replies = [
        Mock(status_code=201, json=Mock(return_value={})),
        Mock(status_code=200, json=Mock(return_value={"email": LOGIN, "enabled": True,
             "inboxAddress": MINUTE, "firstName": "Fixture", "lastName": "User"})),
        Mock(status_code=200, json=Mock(return_value={"address": MINUTE})),
    ]
    session.request.side_effect = replies
    return session, replies


def test_real_protected_store_keeps_login_and_partner_email_distinct(remote):
    session, replies = remote
    row = claru.connect(LOGIN, CLARU_PASSWORD)
    assert row["login_email"] == LOGIN and row["minute_email"] == MINUTE
    assert "password" not in row and CLARU_PASSWORD not in json.dumps(claru.public_accounts())
    assert CLARU_PASSWORD.encode() not in (config.SECRETS_DIR / "claru_accounts.dat").read_bytes()
    assert credential_store.lookup(config.SECRETS_DIR, MINUTE) is None
    assert session.trust_env is False
    assert [c.args[1] for c in session.request.call_args_list] == [
        claru.BASE + "/api/v1/auth/app-sign-in", claru.BASE + "/api/v1/users/me", claru.BASE + "/api/v1/inbox/address"]
    assert all(c.kwargs["allow_redirects"] is False for c in session.request.call_args_list)
    session.request.side_effect = replies
    assert claru.verify_minute(MINUTE)["minute_email"] == MINUTE
    assert session.request.call_args_list[3].kwargs["json"] == {"email": LOGIN, "password": CLARU_PASSWORD}


@pytest.mark.parametrize("field,value", [("email", "other@example.invalid"), ("enabled", False),
    ("inboxAddress", "different@supply.claru.ai"), ("inboxAddress", "generated@other.invalid")])
def test_identity_disabled_or_mismatched_address_never_saves(remote, field, value):
    session, replies = remote
    replies[1].json.return_value[field] = value
    with pytest.raises((claru.ClaruError, ValueError)):
        claru.connect(LOGIN, CLARU_PASSWORD)
    assert not (config.SECRETS_DIR / "claru_accounts.dat").exists()


def test_alias_change_preserves_previous_verified_connection(remote):
    session, replies = remote
    claru.connect(LOGIN, CLARU_PASSWORD)
    path = config.SECRETS_DIR / "claru_accounts.dat"
    before = path.read_bytes()
    replies[1].json.return_value["inboxAddress"] = "changed@supply.claru.ai"
    replies[2].json.return_value["address"] = "changed@supply.claru.ai"
    session.request.side_effect = replies
    with pytest.raises(claru.ClaruError, match="mudou"):
        claru.verify_minute(MINUTE)
    assert path.read_bytes() == before


def test_remote_error_does_not_expose_secret_or_body(remote):
    session, _ = remote
    session.request.side_effect = claru.requests.ConnectionError(CLARU_PASSWORD)
    with pytest.raises(claru.ClaruError) as err:
        claru.connect(LOGIN, CLARU_PASSWORD)
    assert CLARU_PASSWORD not in str(err.value)


def test_corrupt_vault_prevents_remote_call_and_preserves_file(remote):
    session, _ = remote
    path = config.SECRETS_DIR / "claru_accounts.dat"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"invalid-vault")
    with pytest.raises((ValueError, RuntimeError)):
        claru.connect(LOGIN, CLARU_PASSWORD)
    assert path.read_bytes() == b"invalid-vault"
    session.request.assert_not_called()


def prepare(setup, monkeypatch):
    app, _, crowtado, register, root = setup
    monkeypatch.setattr(config, "HOSTINGER_MAIL_PROFILES", [])
    monkeypatch.setattr(claru, "account_for_minute", Mock(return_value={"login_email": LOGIN, "minute_email": MINUTE}))
    monkeypatch.setattr(claru, "verify_minute", Mock(return_value={"login_email": LOGIN, "minute_email": MINUTE}))
    server.Session.from_email.return_value.ensure_auth.return_value = {
        "organizations": [{"resourceKey": config.CLARU_ORG_KEY, "disabled": False}]}
    return app.test_client(), crowtado, register


def test_claru_registration_without_hostinger_uses_only_partner_identity(setup, monkeypatch):
    client, crowtado, register = prepare(setup, monkeypatch)
    result = client.post("/api/accounts/register?async=1", json={"email": MINUTE, "password": MINUTE_PASSWORD,
                                                            "request_id": "fixture-claru-register"})
    assert result.status_code == 200
    row = await_terminal(client)
    assert row["created"] == 1 and row["failed"] == 0
    register.assert_called_once_with(MINUTE, MINUTE_PASSWORD, config.CLARU_INVITE_CODE)
    for call in crowtado.values():
        call.assert_not_called()
    assert credential_store.lookup(config.SECRETS_DIR, MINUTE) == MINUTE_PASSWORD
    assert credential_store.lookup(config.SECRETS_DIR, LOGIN) is None
    assert MINUTE_PASSWORD not in json.dumps(row)
    state = registration_state.load()[MINUTE]
    assert state["state"] == "complete"
    assert state["steps"]["crowtado_signup"]["status"] == "skip"
    assert state["steps"]["minute_register"]["status"] == "ok"


def test_unverified_claru_blocks_before_minute_or_credential_write(setup, monkeypatch):
    client, crowtado, register = prepare(setup, monkeypatch)
    monkeypatch.setattr(claru, "verify_minute", Mock(side_effect=claru.ClaruError("Alias mudou no Claru.")))
    assert client.post("/api/accounts/register?async=1", json={"email": MINUTE, "password": MINUTE_PASSWORD}).status_code == 200
    row = await_terminal(client)
    assert row["failed"] == 1 and row["created"] == 0
    assert not credential_store.record_path(config.SECRETS_DIR, MINUTE).exists()
    register.assert_not_called()
    assert server.account_health.confirmed_ban(MINUTE, {}, {"steps": row["results"][0]["steps"]}) is None


def test_missing_claru_mapping_rejected_before_worker(setup, monkeypatch):
    client, crowtado, register = prepare(setup, monkeypatch)
    monkeypatch.setattr(claru, "account_for_minute", Mock(side_effect=claru.ClaruError("Conecte o Claru.")))
    response = client.post("/api/accounts/register?async=1", json={"email": MINUTE, "password": MINUTE_PASSWORD})
    assert response.status_code == 409
    register.assert_not_called()


def test_claru_organization_pause_preserves_account_and_password(setup, monkeypatch):
    client, crowtado, register = prepare(setup, monkeypatch)
    server.Session.from_email.return_value.ensure_auth.return_value = {
        "organizations": [{"resourceKey": config.CLARU_ORG_KEY, "disabled": True}]}
    assert client.post("/api/accounts/register?async=1", json={"email": MINUTE, "password": MINUTE_PASSWORD}).status_code == 200
    batch = await_terminal(client)
    assert batch["created"] == 0 and batch["failed"] == 1
    registration = {"steps": batch["results"][0]["steps"]}
    issue = server.account_health.confirmed_ban(MINUTE, {}, registration)
    assert issue is None
    assert registration["steps"]["minute_register"]["code"] == "access_paused"
    assert credential_store.lookup(config.SECRETS_DIR, MINUTE) == MINUTE_PASSWORD
    assert client.get("/api/accounts/banned/monitor").json["accounts"] == []
    for method in crowtado.values():
        method.assert_not_called()


def test_claru_resume_uses_saved_minute_access_without_registering_twice(setup, monkeypatch):
    client, crowtado, register = prepare(setup, monkeypatch)
    session = server.Session.from_email.return_value
    session.ensure_auth.side_effect = RuntimeError("fixture temporary membership read failure")
    assert client.post("/api/accounts/register?async=1", json={"email": MINUTE, "password": MINUTE_PASSWORD}).status_code == 200
    assert await_terminal(client)["failed"] == 1
    register.assert_called_once()
    session.ensure_auth.side_effect = None
    session.ensure_auth.return_value = {"organizations": [{"resourceKey": config.CLARU_ORG_KEY, "disabled": False}]}
    assert client.post(f"/api/accounts/{MINUTE}/resume", json={"request_id": "fixture-same-claru-account"}).status_code == 200
    assert await_terminal(client)["created"] == 1
    register.assert_called_once()
    assert minute_api.login.call_count == 2
    minute_api.login.assert_called_with(MINUTE, MINUTE_PASSWORD)


def test_connect_endpoint_and_list_never_return_password(setup, monkeypatch):
    app, *_ = setup
    row = {"login_email": LOGIN, "minute_email": MINUTE, "name": "Fixture User", "checked_at": "2026-10-07"}
    connect = Mock(return_value=row)
    monkeypatch.setattr(claru, "connect", connect)
    monkeypatch.setattr(claru, "public_accounts", Mock(return_value=[row]))
    client = app.test_client()
    response = client.post("/api/accounts/claru/connect", json={"email": LOGIN, "password": CLARU_PASSWORD})
    assert response.status_code == 200
    connect.assert_called_once_with(LOGIN, CLARU_PASSWORD)
    for response in (response, client.get("/api/accounts/claru")):
        assert CLARU_PASSWORD not in response.get_data(as_text=True)
        assert response.json["invite_code"] == "WSNEHSKC"


@pytest.mark.parametrize("accepted", [False, True])
def test_claru_uncertain_signup_resumes_same_identity_without_post_replay(setup, monkeypatch, accepted):
    client, _, register = prepare(setup, monkeypatch)
    if accepted:
        minute_api.login.side_effect = minute_api.AuthError("fixture login unavailable", code="network")
    else:
        error = minute_api.AuthError("fixture response lost", code="network")
        error.remote_effect_possible = True
        register.side_effect = error
    assert client.post("/api/accounts/register?async=1", json={"email": MINUTE, "password": MINUTE_PASSWORD}).status_code == 200
    failed = await_terminal(client)
    assert failed["failed"] == 1 and failed["created"] == 0
    step = registration_state.load()[MINUTE]["steps"]["minute_identity"]
    if accepted:
        assert step["status"] == "ok"
    else:
        assert step["remote_effect_possible"] is True
    assert credential_store.lookup(config.SECRETS_DIR, MINUTE) == MINUTE_PASSWORD
    register.side_effect = AssertionError("An uncertain identity must never be submitted again")
    minute_api.login.side_effect = None
    assert client.post(f"/api/accounts/{MINUTE}/resume", json={}).status_code == 200
    resumed = await_terminal(client)
    assert resumed["created"] == 1 and resumed["failed"] == 0
    register.assert_called_once()
    assert minute_api.login.call_count == (2 if accepted else 1)
    assert MINUTE_PASSWORD not in json.dumps(resumed)


def test_claru_accepted_identity_is_durable_before_login(setup, monkeypatch):
    client, _, register = prepare(setup, monkeypatch)
    def login(email, password):
        state = registration_state.load()[MINUTE]["steps"]
        assert state["minute_identity"]["status"] == "ok"
        assert state["save_partial"]["status"] == "ok"
    minute_api.login.side_effect = login
    assert client.post("/api/accounts/register?async=1", json={"email": MINUTE, "password": MINUTE_PASSWORD}).status_code == 200
    assert await_terminal(client)["created"] == 1
    register.assert_called_once()


def test_crowtado_cooldown_does_not_block_claru_minute(setup, monkeypatch):
    client, remote, register = prepare(setup, monkeypatch)
    server.registration_limits.defer("crowtado", 1800)
    assert client.post("/api/accounts/register?async=1", json={"email": MINUTE, "password": MINUTE_PASSWORD}).status_code == 200
    assert await_terminal(client)["created"] == 1
    register.assert_called_once()
    for call in remote.values():
        call.assert_not_called()


def test_minute_cooldown_blocks_claru_before_remote_work_and_checkpoint(setup, monkeypatch):
    client, _, register = prepare(setup, monkeypatch)
    server.registration_limits.defer("minute", 1800)
    response = client.post("/api/accounts/register?async=1", json={"email": MINUTE, "password": MINUTE_PASSWORD})
    assert response.status_code == 429 and response.json["not_admitted"] is True
    assert response.json["retry_after_seconds"] > 1700
    register.assert_not_called()
    claru.verify_minute.assert_not_called()
    assert not credential_store.record_path(config.SECRETS_DIR, MINUTE).exists()


def test_claru_confirmed_account_ban_is_archived_without_crowtado_calls(setup, monkeypatch):
    client, remote, register = prepare(setup, monkeypatch)
    error = minute_api.AuthError("Account disabled", code="restricted")
    error.provider_error_code = "user_disabled"
    server.Session.from_email.return_value.ensure_auth.side_effect = error
    assert client.post("/api/accounts/register?async=1", json={"email": MINUTE, "password": MINUTE_PASSWORD}).status_code == 200
    batch = await_terminal(client)
    assert batch["created"] == 0 and batch["failed"] == 1 and batch["stopped"] is True
    assert client.get("/api/accounts").json["accounts"] == []
    archived = client.get("/api/accounts/banned/monitor").json["accounts"]
    assert len(archived) == 1 and archived[0]["diagnostic"]["provider"] == "minute"
    assert credential_store.lookup(config.SECRETS_DIR, MINUTE) is None
    for call in remote.values():
        call.assert_not_called()
