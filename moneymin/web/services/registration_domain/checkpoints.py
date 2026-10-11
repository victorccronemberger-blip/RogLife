"""Account-domain implementation; invoked with explicit dependency bindings."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('JsonStateError', '_BULK_REGISTER_LOCK', '_BULK_REGISTER_STATE', '_REGISTRATION_BATCH_RESTORE_BLOCKED', '_REGISTRATION_BATCH_RESTORE_MESSAGE', 'config')


def _save_registration_batch(_services) -> None:
    # Called with the batch lock held. Contains progress only, never passwords.
    if _services._REGISTRATION_BATCH_RESTORE_BLOCKED:
        raise _services.JsonStateError(_services._REGISTRATION_BATCH_RESTORE_MESSAGE)
    from moneymin.web import registration_batch
    registration_batch.save(_services.config.DATA_DIR / "account_registration_batch.json", _services._BULK_REGISTER_STATE)

def _restore_registration_batch(_services) -> None:
    from moneymin.web import registration_batch
    pass  # state writes use declared dependencies
    try:
        saved = registration_batch.load(_services.config.DATA_DIR / "account_registration_batch.json")
    except (OSError, _services.JsonStateError):
        # Keep the original bytes untouched. A malformed checkpoint cannot be
        # interpreted as an empty queue because that could replay an identity
        # whose remote creation already happened.
        with _services._BULK_REGISTER_LOCK:
            _services._BULK_REGISTER_STATE.clear()
            _services._BULK_REGISTER_STATE.update({
                "state": "failed", "total": 0, "completed": 0,
                "created": 0, "failed": 0, "results": [],
                "current_email": "", "current_step": "", "domain": "",
                "stopped": True, "restore_blocked": True,
                "error": _services._REGISTRATION_BATCH_RESTORE_MESSAGE,
            })
            _services._REGISTRATION_BATCH_RESTORE_BLOCKED = True
        return
    _services._REGISTRATION_BATCH_RESTORE_BLOCKED = False
    if saved["state"] in ("running", "stopping"):
        saved.update(state="failed", current_email="", current_step="",
                     error="O serviço reiniciou durante o cadastro. Confira as contas incompletas e retome a mesma conta.")
    with _services._BULK_REGISTER_LOCK:
        _services._BULK_REGISTER_STATE.clear()
        _services._BULK_REGISTER_STATE.update(saved)

def _mark_bulk_account_removed(_services, email: str) -> None:
    """Mantém o histórico do lote, mas torna sua ação de remoção idempotente."""
    normalized = email.strip().casefold()
    with _services._BULK_REGISTER_LOCK:
        for result in _services._BULK_REGISTER_STATE.get("results", []):
            if (isinstance(result, dict)
                    and str(result.get("email") or "").strip().casefold() == normalized):
                result["removed"] = True
                result["removable"] = False

def _step_ok(_services, detail: str = "") -> dict[str, str]:
    return {"status": "ok", "detail": detail}

def _step_skip(_services, detail: str = "") -> dict[str, str]:
    return {"status": "skip", "detail": detail}

def _step_fail(_services, detail: str = "") -> dict[str, str]:
    return {"status": "fail", "detail": detail}

