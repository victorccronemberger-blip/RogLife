"""Pre-v2.89 scoped restrictions cannot silently become permanent exclusions."""
from unittest.mock import Mock

import pytest

from moneymin import account_bans, config, credential_store, token_store
from moneymin.atomic_io import save_json
from moneymin.web import account_health, server
from test_confirmed_ban_exclusion import local, BAN, KEEP, UNKNOWN, PAYOUT, diagnosis


def test_accounts_read_keeps_legacy_health_without_provider_evidence(local, monkeypatch):
    history = server._load_account_health_history()
    del history[BAN]["issue"]["provider_error_code"]
    save_json(server.ACCOUNT_HEALTH_PATH, history)
    assert account_health.confirmed_ban(BAN, history[BAN]) is None
    token_path = token_store.record_path(config.SECRETS_DIR, BAN)
    password_path = credential_store.record_path(config.SECRETS_DIR, BAN)
    before = (token_path.read_bytes(), password_path.read_bytes(), server.ACCOUNT_HEALTH_PATH.read_bytes())
    archive = Mock(wraps=server._ban_accounts)
    monkeypatch.setattr(server, "_ban_accounts", archive)
    response = server.create_app(for_testing=True).test_client().get("/api/accounts")
    assert response.status_code == 200
    assert {row["email"] for row in response.json["accounts"]} == {BAN, KEEP, UNKNOWN, PAYOUT}
    row = next(row for row in response.json["accounts"] if row["email"] == BAN)
    assert row["restriction"]["code"] == "historical_restriction"
    assert row["restriction"]["confirmed"] is False
    archive.assert_not_called()
    assert account_bans.banned_emails() == set()
    assert before == (token_path.read_bytes(), password_path.read_bytes(), server.ACCOUNT_HEALTH_PATH.read_bytes())


@pytest.mark.parametrize("old_step,new_step,code", [
    ("minute_identity", "minute_register", "access_paused"),
    ("validate", "minute_register", "access_paused"),
    ("crowtado_signup", "ban_check", "account_locked"),
])
def test_batch_without_health_does_not_promote_old_ambiguous_step(old_step, new_step, code):
    registration = {"steps": {
        old_step: {"status": "fail", "code": "restricted"},
        new_step: {"status": "fail", "code": code},
    }}
    assert account_health.confirmed_ban(BAN, {}, registration) is None


@pytest.mark.parametrize("provider_code", ["user_banned", "user_account_disabled", "user_disabled"])
def test_explicit_provider_evidence_still_confirms_global_ban(provider_code):
    check = diagnosis(BAN, "disabled")
    check["issue"]["provider_error_code"] = provider_code
    issue = account_health.confirmed_ban(BAN, check)
    assert issue["restriction_confirmed"] is True
    assert issue["provider_error_code"] == provider_code
