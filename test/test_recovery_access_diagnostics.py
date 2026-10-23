"""Recovery diagnostics only: no resume, credentials, network or ADB."""
import json
from unittest.mock import Mock

import pytest

from moneymin import recovery
from moneymin.minute_api import AuthError


@pytest.mark.parametrize("code,guidance", [
    ("access_paused", "Isso não confirma banimento"),
    ("app_check", "App Check autorizada"),
    ("policy", "política de integração e gravação"),
    ("version", "Atualize o QMoney"),
    ("tls", "Não desative a validação de certificados"),
    ("uber_device", "fluxo Uber"),
    ("dns", "Não foi possível resolver o endereço do serviço. Confira a conexão e o DNS e tente novamente. Isso não indica senha incorreta."),
    ("unknown", "Não foi possível determinar a causa da falha Minute. Verifique novamente; se persistir, consulte o suporte. Não remova a conta nem altere a senha por este diagnóstico."),
])
@pytest.mark.parametrize("stage", ["minute_authentication", "organization_check", "pending_uploads"])
def test_typed_access_diagnostic_survives_stage_without_private_text(monkeypatch, code, guidance, stage):
    error = AuthError("private-token https://example.invalid/?secret=password", code=code)

    def fail(*args, **kwargs):
        kwargs["on_stage"](stage)
        raise error

    resume = Mock(side_effect=fail)
    monkeypatch.setattr(recovery, "resume_account", resume)
    resolve_org = Mock(side_effect=AssertionError("Must not resolve a real account"))
    runner = recovery.RecoveryRunner()
    runner._run("fixture@example.invalid", resolve_org, "fixture-session")
    state = runner.snapshot()
    assert state["state"] == "error"
    assert state["error_stage"] == stage
    assert state["error_category"] == f"minute_{code}"
    assert guidance in state["error"]
    assert "Os registros foram preservados" in state["error"]
    assert "private-token" not in json.dumps(state)
    assert "secret=password" not in json.dumps(state)
    assert str(error) not in state["error"]
    resume.assert_called_once()
    resolve_org.assert_not_called()


@pytest.mark.parametrize("code", [
    "authentication", "service", "restricted", "invalid_response", "identity",
    "network", "timeout", "missing_access", "rate_limit", "forbidden", "device", "organization",
])
def test_existing_auth_categories_are_preserved(code):
    assert recovery.RecoveryRunner._error_category(AuthError("private", code=code), "journal_review") == f"minute_{code}"


def test_unknown_auth_code_and_message_are_not_exported(monkeypatch):
    error = AuthError("private-message", code="private-code")
    monkeypatch.setattr(recovery, "resume_account", Mock(side_effect=error))
    runner = recovery.RecoveryRunner()
    runner._run("fixture@example.invalid", Mock())
    state = runner.snapshot()
    assert state["error_category"] == "minute_authentication"
    assert "private" not in json.dumps(state)
