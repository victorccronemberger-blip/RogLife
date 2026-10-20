"""Sanitized error categories; never expose remote response bodies or credentials."""
from __future__ import annotations
import json
import re
import urllib.error

_FAILURE_NOT_CONFIGURED = "not_configured"

_FAILURE_ATTESTATION = "attestation_failed"

_FAILURE_DEVICE = "device_capture_failed"

_FAILURE_PERMISSION = "permission_denied"

_FAILURE_QUOTA = "quota_exhausted"

_FAILURE_INVALID = "invalid_response"

_FAILURE_EXCHANGE = "exchange_failed"

_ATTESTATION_COOLDOWN_S = 3600.0

_QUOTA_COOLDOWN_S = 70.0

_NON_RETRYABLE_FAILURES = {_FAILURE_ATTESTATION, _FAILURE_PERMISSION}

_FAILURE_MESSAGES = {
    _FAILURE_NOT_CONFIGURED: "App Check não está configurado.",
    _FAILURE_ATTESTATION: "Firebase recusou a atestação na troca de App Check.",
    _FAILURE_DEVICE: (
        "Não foi possível obter App Check Play Integrity do celular via ADB. "
        "Mantenha o S22 conectado, com Minute aberto/logado e root, e use Atualizar."
    ),
    _FAILURE_PERMISSION: "Firebase negou permissão na troca de App Check.",
    _FAILURE_QUOTA: "A cota de troca de App Check foi excedida.",
    _FAILURE_INVALID: "A configuração ou resposta de App Check é inválida.",
    _FAILURE_EXCHANGE: "Não foi possível concluir a troca de App Check.",
}

_FIREBASE_STATUSES = {
    "CANCELLED", "UNKNOWN", "INVALID_ARGUMENT", "DEADLINE_EXCEEDED",
    "NOT_FOUND", "ALREADY_EXISTS", "PERMISSION_DENIED", "RESOURCE_EXHAUSTED",
    "FAILED_PRECONDITION", "ABORTED", "OUT_OF_RANGE", "UNAUTHENTICATED",
    "INTERNAL", "UNAVAILABLE", "DATA_LOSS",
}

class AppCheckExchangeError(RuntimeError):
    """Safe, structured Firebase exchange failure without a response body."""

    def __init__(self, category: str, *, http_status: int | None = None,
                 firebase_status: str | None = None):
        if (not isinstance(category, str) or category not in _FAILURE_MESSAGES
                or category == _FAILURE_NOT_CONFIGURED):
            category = _FAILURE_EXCHANGE
        if type(http_status) is not int or not 100 <= http_status <= 599:
            http_status = None
        if not isinstance(firebase_status, str) or firebase_status not in _FIREBASE_STATUSES:
            firebase_status = None
        self.category = category
        self.http_status = http_status
        self.firebase_status = firebase_status
        super().__init__(category)

def _exchange_http_diagnostic(exc: urllib.error.HTTPError) -> dict:
    try:
        status = int(getattr(exc, "code", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        status = None
    if status is None or not 100 <= status <= 599:
        status = None
    try:
        raw = exc.read(4096)
    except Exception:
        raw = b""
    message = ""
    firebase_status = ""
    try:
        payload = json.loads(raw.decode("utf-8", "replace")) if raw else {}
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            message = str(error.get("message") or "")
            firebase_status = str(error.get("status") or "")
    except (ValueError, TypeError, UnicodeError):
        pass
    normalized = re.sub(r"[ _-]", "", f"{message} {firebase_status}").casefold()
    if status == 403 and "appattestationfailed" in normalized:
        category = _FAILURE_ATTESTATION
    elif status == 403:
        # Permission denial alone does not identify the failed authorization rule.
        category = _FAILURE_PERMISSION
    elif status == 429 or "resourceexhausted" in normalized or "quota" in normalized:
        category = _FAILURE_QUOTA
    else:
        category = _FAILURE_EXCHANGE
    return {
        "category": category,
        "http_status": status,
        "firebase_status": (firebase_status if isinstance(firebase_status, str)
                            and firebase_status in _FIREBASE_STATUSES else None),
    }

def is_app_check_rejection(text: str) -> bool:
    """Only explicit markers qualify; a generic 401/403 is not App Check evidence."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    detail = data.get("detail", data)
    if isinstance(detail, dict):
        detail = ' '.join(str(detail.get(k, '')) for k in ('error', 'code', 'message'))
    if not isinstance(detail, str):
        return False
    normalized = re.sub(r"[ _-]", "", detail).casefold()
    return 'appcheck' in normalized

def is_app_check_blocked_reason(value: object) -> bool:
    """True only for the explicit Minute ``X-Blocked-Reason: app_check`` marker."""
    return isinstance(value, str) and value.casefold() == "app_check"
