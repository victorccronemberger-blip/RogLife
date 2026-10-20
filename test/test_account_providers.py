"""Provider corrections must affect routing, not just a display label."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from moneymin import account_providers, config, org_policy
from moneymin.atomic_io import JsonStateError, save_json
from moneymin.web.services.accounts_domain import persistence


@pytest.fixture
def local_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def test_explicit_claru_identity_preserves_login_and_other_accounts(local_accounts):
    email = "owner@example.invalid"
    account_providers.assign_kind(" OWNER@EXAMPLE.INVALID ", "claru")
    assert org_policy.account_kind(email) == "claru"
    assert org_policy.account_kind("another@example.invalid") == "crowtado"
    assert org_policy.account_kind("partner@supply.claru.ai") == "claru"
    assert org_policy.target_org_key(email) == config.CLARU_ORG_KEY
    assert org_policy.target_invite(email) == config.CLARU_INVITE_CODE
    session = Mock()
    both = [{"resourceKey": key} for key in (config.ORG_KEY, config.CLARU_ORG_KEY)]
    assert org_policy.ensure_membership(session, email, both) == config.CLARU_ORG_KEY
    with pytest.raises(RuntimeError, match="Claru ausente"):
        org_policy.ensure_membership(session, email, both[:1])
    session.join_org.assert_not_called()
    account_providers.assign_kind("other@example.invalid", "crowtado")
    assert org_policy.account_kind(email) == "claru"


@pytest.mark.parametrize("row", [
    {"schema": 1, "email": "different@example.invalid", "kind": "claru"},
    {"schema": True, "email": "owner@example.invalid", "kind": "claru"},
    {"schema": 1, "email": "owner@example.invalid", "kind": "unknown"},
    {"schema": 1, "email": "owner@example.invalid", "kind": []},
])
def test_invalid_assignment_is_preserved_and_never_falls_back(local_accounts, row):
    email = "owner@example.invalid"
    path = account_providers._path(email)
    save_json(path, row)
    before = path.read_bytes()
    with pytest.raises(JsonStateError):
        org_policy.account_kind(email)
    with pytest.raises(JsonStateError):
        account_providers.assign_kind(email, "claru")
    assert path.read_bytes() == before


def test_invalid_assignment_input_creates_no_file(local_accounts):
    for email, kind in (("invalid", "claru"), ("owner@example.invalid", "unknown")):
        with pytest.raises(ValueError):
            account_providers.assign_kind(email, kind)
    assert not list(local_accounts.rglob("*.json"))


def test_accounts_list_uses_provider_without_claiming_cached_wrong_membership(local_accounts):
    email = "owner@example.invalid"
    other = "other@example.invalid"
    account_providers.assign_kind(email, "claru")
    bindings = SimpleNamespace(
        config=config, org_policy=org_policy,
        _load_prefs=lambda: {"org_keys": {email: config.ORG_KEY, other: config.ORG_KEY}},
        _load_account_health_history=lambda: {}, _removed_accounts=lambda: set(),
        registration_state=SimpleNamespace(load=lambda: {}),
        token_store=SimpleNamespace(records=lambda _: {
            value: (None, {"email": value}) for value in (email, other)}),
        account_health=SimpleNamespace(confirmed_ban=lambda *args: False),
    )
    rows = persistence._list_accounts(bindings)
    assert len(rows) == 2
    assert rows[0]["email"] == email and rows[0]["account_kind"] == "claru"
    assert rows[0]["org_key"] is None and rows[0]["org_name"] == "Não verificada"
    assert rows[1]["account_kind"] == "crowtado" and rows[1]["org_key"] == config.ORG_KEY
