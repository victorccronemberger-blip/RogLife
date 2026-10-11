"""An ambiguous registration checkpoint must not stop app startup or replay work."""
import json
from unittest.mock import patch

import pytest

from moneymin import config
from moneymin.atomic_io import JsonStateError
from moneymin.web import registration_batch, server


@pytest.fixture
def checkpoint(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    path = data / "account_registration_batch.json"
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(server, "_BULK_REGISTER_STATE", {"state": "idle"})
    monkeypatch.setattr(server, "_REGISTRATION_BATCH_RESTORE_BLOCKED", False)
    return path


def _legacy_terminal_checkpoint():
    return json.dumps({
        "state": "done", "total": 1, "completed": 1, "created": 0,
        "failed": 1, "results": [], "current_email": "", "current_step": "",
        "domain": "example.invalid", "stopped": False, "request_id": "",
        "request_fingerprint": "a" * 64,
    }, separators=(",", ":")).encode()


@pytest.mark.parametrize("raw", [_legacy_terminal_checkpoint(), b'{"state":'])
def test_ambiguous_or_corrupt_checkpoint_starts_app_but_blocks_every_creator(
        checkpoint, raw, monkeypatch):
    checkpoint.write_bytes(raw)
    monkeypatch.setattr(server, "_full_register_account",
                        lambda *_args, **_kwargs: pytest.fail("creator must remain blocked"))

    assert checkpoint.read_bytes() == raw
    with patch.object(server, "_require_local_api_token", return_value="fixture-token"):
        app = server.create_app()
    assert server._REGISTRATION_BATCH_RESTORE_BLOCKED is True
    client = app.test_client()
    headers = {"X-QMoney-Session": "fixture-token"}
    assert client.get("/", headers=headers).status_code == 200
    status = client.get("/api/accounts/bulk-register/status", headers=headers).get_json()
    assert status["state"] == "failed" and status["restore_blocked"] is True
    assert "revise o progresso" in status["error"]
    preflight = client.get("/api/accounts/bulk-register/preflight", headers=headers).get_json()
    assert preflight["ready"] is False
    assert preflight["checks"]["registration_progress"]["ok"] is False

    for path, body in (
        ("/api/accounts/register", {"email": "new@example.invalid", "password": "fixture"}),
        ("/api/accounts/register?async=1", {"email": "new@example.invalid", "password": "fixture"}),
        ("/api/accounts/bulk-register", {"count": 1, "domain": "example.invalid"}),
        ("/api/accounts/old@example.invalid/resume", {}),
    ):
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == 503
        assert response.get_json()["code"] == "registration_progress_restore_blocked"
        assert response.get_json()["not_admitted"] is True
    assert checkpoint.read_bytes() == raw
    with pytest.raises(JsonStateError):
        server._save_registration_batch()
    assert checkpoint.read_bytes() == raw


def test_save_refuses_to_replace_ambiguous_checkpoint(checkpoint):
    raw = _legacy_terminal_checkpoint()
    checkpoint.write_bytes(raw)
    candidate = {
        "state": "idle", "total": 0, "completed": 0, "created": 0,
        "failed": 0, "results": [],
    }

    with pytest.raises(JsonStateError):
        registration_batch.save(checkpoint, candidate)
    assert checkpoint.read_bytes() == raw


def test_checkpoint_read_oserror_blocks_creator_without_changing_bytes(checkpoint, monkeypatch):
    raw = _legacy_terminal_checkpoint()
    checkpoint.write_bytes(raw)

    def fail_load(_path):
        raise OSError("fixture read failure")

    monkeypatch.setattr(registration_batch, "load", fail_load)

    server._restore_registration_batch()

    assert server._REGISTRATION_BATCH_RESTORE_BLOCKED is True
    assert checkpoint.read_bytes() == raw


def test_valid_restore_clears_previous_barrier_without_running_work(checkpoint, monkeypatch):
    valid = {"state": "failed", "total": 0, "completed": 0,
             "created": 0, "failed": 0, "results": [],
             "stopped": True, "error": "Fixture stopped."}
    checkpoint.write_text(json.dumps(valid), encoding="utf-8")
    monkeypatch.setattr(server, "_REGISTRATION_BATCH_RESTORE_BLOCKED", True)
    monkeypatch.setattr(server, "_save_registration_batch",
                        lambda: pytest.fail("restore must not rewrite or run the checkpoint"))

    server._restore_registration_batch()

    assert server._REGISTRATION_BATCH_RESTORE_BLOCKED is False
    assert server._BULK_REGISTER_STATE == valid
