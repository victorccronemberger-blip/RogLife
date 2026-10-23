"""
minute_api.py — Cliente da minute-api (app Minute, com.bakerdata.minute).

Biblioteca (sem efeitos de CLI). A interface de linha de comando fica em
`scripts/minute_cli.py`.

Cobre autenticação (Firebase Identity Toolkit), sessão com refresh automático de
token e chamadas à API documentadas em `reference/openapi.json` (98 endpoints).
Somente stdlib.

Exemplo:
    from moneymin.minute_api import login, Session
    login("user@example.com", "senha")          # grava secrets/token_user_at_example_com.json
    s = Session.from_email("user@example.com")   # carrega + SEMPRE troca o idToken no Firebase
    me = s.get("/api/v1/users/me")
    tasks = s.all_tasks(org_key)  # categorias vêm embutidas em cada task (1.29)
"""
from __future__ import annotations

import base64
from email.utils import parsedate_to_datetime
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, parse_qs

from . import config, credential_store, device_profile, transport, token_store
from .atomic_io import JsonStateError, load_json_state, save_json
from .service_policy import RecordingPolicy

logger = logging.getLogger(__name__)


def minute_lang_code() -> str:
    """Código i18n do app Minute (`en`|`es`|`pt`) para `?langCode=`.

    O Hermes `withLangCode` omite o parâmetro quando o idioma é `en`.
    `ACCEPT_LANGUAGE` HTTP (ex. pt-BR) é mapeado para o código curto do app.
    """
    raw = (config.ACCEPT_LANGUAGE or "").split(",", 1)[0].strip().lower()
    if not raw or raw.startswith("en"):
        return ""
    if raw.startswith("pt"):
        return "pt"
    if raw.startswith("es"):
        return "es"
    primary = raw.split("-", 1)[0]
    return primary if primary in ("pt", "es", "en") else ""


def _tasks_lang_query(*, canonical: bool = False) -> str:
    # Selection rules and the portable Ego4D index use canonical English task
    # names. A translated name must never become a different activity key.
    if canonical:
        return "?langCode=en"
    code = minute_lang_code()
    if not code or code == "en":
        return ""
    return f"?langCode={code}"


def categories_from_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrega `categories[{slug,label}]` embutidas nas tasks (prática 1.29).

    Substitui o endpoint global `/api/v1/categories` como fonte primária.
    """
    by_slug: dict[str, dict[str, Any]] = {}
    for task in tasks:
        for cat in task.get("categories") or []:
            if not isinstance(cat, dict):
                continue
            slug = cat.get("slug")
            if not isinstance(slug, str) or not slug.strip():
                continue
            key = slug.strip()
            if key not in by_slug:
                by_slug[key] = {
                    "slug": key,
                    "label": cat.get("label") or key,
                    "id": cat.get("id") or key,
                    "name": cat.get("label") or cat.get("name") or key,
                }
    return list(by_slug.values())

# Folga antes do exp do JWT: PUT de blob pode passar de 2 min; 10 min evita
# mandar um Bearer que morre no meio do upload.
_REFRESH_SKEW_S = 10 * 60
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_VERSION_GATE_LOCK = threading.RLock()
_VERSION_GATE_MEMORY: dict[str, dict[str, Any]] = {}

# --- Endpoints Firebase / Google Identity Toolkit ---------------------------
_SIGNIN_URL = (
    "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
    f"?key={config.FIREBASE_API_KEY}"
)
_REFRESH_URL = f"https://securetoken.googleapis.com/v1/token?key={config.FIREBASE_API_KEY}"


class AuthError(RuntimeError):
    """Falha de autenticação (token ausente, expirado sem refresh válido, etc.)."""

    def __init__(self, message: str, *, code: str | None = None,
                 http_status: int | None = None,
                 retry_after_seconds: float | None = None,
                 provider_error_code: str | None = None):
        super().__init__(message)
        self.account_issue_code = code
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds
        self.remote_effect_possible = False
        self.provider_error_code = provider_error_code


def _retry_after_seconds(headers: dict[str, str] | None) -> float | None:
    """Parse Retry-After without retaining arbitrary response headers."""
    value = next((str(item) for key, item in (headers or {}).items()
                  if str(key).casefold() == "retry-after"), "").strip()
    if not value:
        return None
    try:
        seconds = float(value) if re.fullmatch(r"\d+(?:\.\d+)?", value) else (
            parsedate_to_datetime(value).timestamp() - time.time())
        return max(0.0, seconds) if seconds < float("inf") else None
    except (ValueError, TypeError, OverflowError):
        return None


_MINUTE_DUPLICATE_MARKERS = {
    "email_exists", "email_already_exists", "form_identifier_exists",
    "email_already_in_use", "email_already_taken",
    "email_already_exists_message", "email_is_already_taken",
}


def _minute_duplicate_marker(body: str) -> str | None:
    """Accept only known duplicate codes or exact legacy phrases."""
    phrase_markers = {
        "email already exists", "email address already exists",
        "email is already taken", "email address is already taken",
        "email address is taken", "email already in use",
        "email address is already in use", "account already exists",
        "conta ja existe", "e mail ja existe", "email ja existe",
        "e mail ja esta em uso", "email ja esta em uso",
        "endereco de e mail ja esta em uso",
    }

    def normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()

    values: list[tuple[str, str]] = []
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError, ValueError):
        payload = None

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized_key = str(key).casefold()
                if isinstance(item, str) and normalized_key in {
                        "code", "error_code", "error", "detail", "message"}:
                    values.append((normalized_key, item))
                elif isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    if payload is None:
        values.append(("body", body))
    elif isinstance(payload, str):
        values.append(("body", payload))
    else:
        visit(payload)

    for key, raw in values:
        value = normalize(raw)
        code = value.replace(" ", "_")
        if key in {"code", "error_code", "error", "body"} and code in _MINUTE_DUPLICATE_MARKERS:
            return "EMAIL_EXISTS"
        if value in phrase_markers:
            return "EMAIL_EXISTS"
    return None


def validate_task_catalog(rows: Any) -> list[dict[str, Any]]:
    """Validate fields consumed by selection without echoing remote content."""
    invalid = AuthError("Catálogo retornou campos de tarefa inválidos.",
                        code="invalid_response")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise invalid
    for row in rows:
        if "requiresAudio" in row and type(row["requiresAudio"]) is not bool:
            raise invalid
        if any(row.get(key) is not None and not isinstance(row[key], str)
               for key in ("name", "description")):
            raise invalid
        categories = row.get("categories")
        if categories is None:
            continue
        if not isinstance(categories, list) or any(not isinstance(item, dict) for item in categories):
            raise invalid
        for item in categories:
            if any(item.get(key) is not None and not isinstance(item[key], str)
                   for key in ("slug", "label")):
                raise invalid
    return rows


def _auth_failure(status: int, body: str, stage: str, *, firebase: bool = False,
                  headers: dict[str, str] | None = None, profile_read: bool = False) -> AuthError:
    """Preserva a causa sem confundir indisponibilidade com senha ou bloqueio."""
    raw = (body or "").casefold()
    code = "unknown"
    provider_error_code = None
    if status == 429:
        code = "rate_limit"
    elif status >= 500:
        code = "service"
    elif status == 408 or (status == -1 and any(s in raw for s in ("timeout", "timed out"))):
        code = "timeout"
    elif status == -1:
        code = "tls" if any(s in raw for s in ("certificate", "ssl", "tls")) else "network"
    elif status == 401:
        code = "authentication"
    elif status == 403:
        code = _classify_minute_403(body) or "forbidden"
    from . import appcheck
    blocked = next((str(value).casefold() for key, value in (headers or {}).items()
                    if key.casefold() == "x-blocked-reason"), "")
    if not firebase:
        if appcheck.is_app_check_blocked_reason(blocked) or (
                status in (401, 403) and appcheck.is_app_check_rejection(body)):
            code = "app_check"
        elif status in (401, 403) and code == "forbidden" and blocked in ("device", "uber-device"):
            code = "device" if blocked == "device" else "uber_device"
    if firebase and status in (400, 401, 403):
        try:
            payload = json.loads(body)
            error = payload.get("error") if isinstance(payload, dict) else None
            message = error.get("message") if isinstance(error, dict) else error
        except (ValueError, TypeError):
            message = None
        if message == "USER_DISABLED":
            code = "restricted"
            provider_error_code = "user_disabled"
        elif message in ("INVALID_PASSWORD", "INVALID_LOGIN_CREDENTIALS", "INVALID_REFRESH_TOKEN",
                         "TOKEN_EXPIRED", "USER_NOT_FOUND", "EMAIL_NOT_FOUND", "INVALID_GRANT"):
            code = "authentication"
        elif message == "TOO_MANY_ATTEMPTS_TRY_LATER":
            code = "rate_limit"
    elif status in (401, 403) and code == "restricted":
        provider_error_code = "user_account_disabled"
    message = f"{stage}: resposta HTTP {status}; verificação não concluída."
    if code == "version":
        detail = _parse_blocked_detail(body) or {}
        message = _version_update_message(detail.get("min_version"))
    error = AuthError(
        message,
        code=code,
        http_status=status,
        retry_after_seconds=_retry_after_seconds(headers) if status == 429 else None,
        provider_error_code=provider_error_code,
    )
    error.profile_read = profile_read
    # Only known protocol markers may reach logs/UI; never retain response
    # bodies, authentication headers or arbitrary remote strings here.
    error.blocked_reason = (
        blocked if blocked in ("user", "device", "uber-device", "app_check") else None
    )
    return error


@dataclass(frozen=True)
class HttpResponse:
    """Resposta HTTP completa para decisões de retry e bloqueio."""

    status: int
    text: str
    headers: dict[str, str]


def _as_list(body: Any, keys: tuple[str, ...] = ("tasks", "items", "data", "results")) -> list:
    """Normaliza respostas que às vezes vêm como lista e às vezes como objeto.

    Aceita o texto cru da resposta, um dict ou uma lista e devolve sempre uma
    lista (vazia se não houver nada utilizável).
    """
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return []
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in keys:
            if isinstance(body.get(key), list):
                return body[key]
    return []


# --- HTTP baixo nível --------------------------------------------------------
def _request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: Any = None,
    timeout: int = 30,
    raw_form: bool = False,
) -> tuple[int, str]:
    """Executa uma requisição HTTP e devolve (status, corpo_texto).

    O transporte (TLS/HTTP fingerprint) vem de `transport.py`: curl_cffi com
    impersonate Chrome/Android se instalado, senão urllib stdlib. Nunca levanta
    exceção de rede: em erro devolve (-1, "ERRO: ...").
    """
    headers = dict(headers or {})
    if isinstance(body, (dict, list)):
        data = json.dumps(body).encode()
    elif isinstance(body, str):
        data = body.encode()
    else:
        data = None

    if data and not raw_form and not any(k.lower() == "content-type" for k in headers):
        headers["Content-Type"] = "application/json"

    try:
        status, raw = transport.http_request(method, url, headers=headers,
                                             body=data, timeout=timeout)
        return status, raw.decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 — cliente devolve erro em vez de propagar
        return -1, f"ERRO: {exc}"


def _is_geo_route(path: str, method: str = "GET") -> bool:
    """Exact Minute 1.29 callsites; query strings never select a location route.

    The profile omits the header when no valid sample is configured. APK
    createUpload and getSas request deviceLocationHeader (L357622–357731).
    """
    route = path.split("?", 1)[0].split("#", 1)[0].casefold()
    verb = str(method).upper()
    if verb == "POST":
        return route in {"/api/v1/uploads", "/api/v1/storage/sas/blobs"}
    if verb != "GET":
        return False
    parts = route.strip("/").split("/")
    return (route == "/api/v1/auth/uber/eligibility"
            or (len(parts) == 5 and parts[:3] == ["api", "v1", "orgs"]
                and bool(parts[3]) and parts[4] == "quota"))


def _write_operation_phase(method: str, path: str) -> str | None:
    """Return the delivery phase for write ops that require local App Check."""
    method_u = str(method or "").upper()
    if method_u not in ("POST", "PUT", "PATCH"):
        return None
    route = path.split("?", 1)[0].split("#", 1)[0].rstrip("/") or "/"
    parts = [part for part in route.strip("/").split("/") if part]
    if method_u == "POST" and route == "/api/v1/uploads":
        return "create"
    if method_u == "POST" and route == "/api/v1/storage/sas/blobs":
        return "sas"
    if method_u == "POST" and parts and parts[-1] == "finalize":
        return "finalize"
    if method_u == "PATCH" and parts and parts[-1] == "complete":
        return "complete"
    if method_u == "POST" and parts and parts[-1] == "evaluate":
        return "evaluate"
    if method_u == "POST" and parts and parts[-1] == "fail":
        return "fail"
    if method_u == "PUT" and route == "/api/v1/devices/push-token":
        return "push-token"
    return "write"


def _device_appcheck_renewable() -> bool:
    """True when the connected phone can mint App Check tokens for writes."""
    from . import roglife_appcheck
    return bool(roglife_appcheck.device_mode_enabled())


def require_app_check_for_write(method: str, path: str) -> dict[str, str]:
    """Attach App Check, or report that the target write was not sent."""
    from . import appcheck
    from .upload_types import UploadError

    phase = _write_operation_phase(method, path)
    appcheck_header = appcheck.get_app_check_header()
    if phase is None or appcheck_header:
        return appcheck_header

    # Device mode: a missing/expired token is renewable from the connected
    # phone. Renew once (soft read; Minute restart below the floor) so an
    # in-flight campaign never dies on a write that could be authorized.
    if _device_appcheck_renewable():
        appcheck.ensure_app_check_fresh()
        appcheck_header = appcheck.get_app_check_header()
        if appcheck_header:
            return appcheck_header

    status = appcheck.app_check_status()
    diagnostic = status.get("diagnostic") or {}
    failure = diagnostic.get("category") or status.get("failure") or "not_configured"
    message = diagnostic.get("message") or "App Check não disponível"
    route = path.split("?", 1)[0]
    logger.warning(
        "Requisição %s %s interrompida: App Check indisponível (%s). "
        "Token available: %s, source: %s, left_s: %s",
        str(method or "").upper(),
        route,
        failure,
        status.get("token_available"),
        status.get("source"),
        (status.get("device") or {}).get("left_s"),
    )
    raise UploadError(
        f"App Check indisponível: {message}. "
        f"Causa: {failure}. "
        f"Renove o token antes de tentar novamente.",
        transient=False,
        phase=phase,
        blocked_reason="app_check",
        review_required=False,
        appcheck_cause=failure if isinstance(failure, str) else "not_configured",
        appcheck_message=message if isinstance(message, str) else "App Check não disponível",
        request_not_sent=True,
    )


# --- Version gate (kill-switch por 403, réplica do maybeLatchVersionGate) -----
# Hermes: detail.error === 'app_version_too_old' e detail.min_version parseável.

_APP_VERSION_TOO_OLD = "app_version_too_old"
_BLOCKED_DETAIL_ERRORS = {
    _APP_VERSION_TOO_OLD: "version",
    "device": "device",
    "uber-device": "uber_device",
}


def _version_gate_file() -> Path:
    return config.DATA_DIR / "version-gate.json"


def _parse_blocked_detail(text: str) -> dict[str, Any] | None:
    """Extrai `detail` de um corpo 403 no formato do app (`blockedDetailSchema`)."""
    try:
        body = json.loads(text) if text and str(text).strip() else {}
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if not isinstance(body, dict):
        return None
    detail = body.get("detail")
    return detail if isinstance(detail, dict) else None


def _classify_minute_403(text: str) -> str | None:
    """Mapeia `detail.error` do Minute para código de diagnóstico."""
    detail = _parse_blocked_detail(text)
    if not detail:
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            payload = text
        message = payload.get("detail") if isinstance(payload, dict) else payload
        if (isinstance(message, str)
                and message.strip().casefold().rstrip(".") == "user account is disabled"):
            return "restricted"
        return None
    message = detail.get("message")
    if (isinstance(message, str)
            and message.strip().casefold().rstrip(".") == "user account is disabled"):
        return "restricted"
    error = detail.get("error")
    if not isinstance(error, str):
        return None
    return _BLOCKED_DETAIL_ERRORS.get(error)


def _parse_semver_or_none(value: Any) -> tuple[int, int, int] | None:
    """Aceita somente os três componentes numéricos do contrato Hermes."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
        return None
    try:
        major, minor, patch = map(int, value.split("."))
    except ValueError:
        return None
    return major, minor, patch


def _version_gate_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))

def _validated_version_gate(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict)
            or _parse_semver_or_none(value.get("minVersion")) is None
            or ("appVersion" in value and _parse_semver_or_none(value["appVersion"]) is None)
            or ("latchedAt" in value and (type(value["latchedAt"]) is not int or value["latchedAt"] < 0))):
        raise ValueError
    return dict(value)

def _read_version_gate_locked(path: Path) -> dict[str, Any] | None:
    try:
        if path.is_symlink():
            raise ValueError
        value = load_json_state(path, None)
        return None if value is None else _validated_version_gate(value)
    except (JsonStateError, OSError, UnicodeError, ValueError):
        raise AuthError(
            "A trava de versão local está inválida ou ilegível. Preserve o arquivo e restaure um estado válido antes de continuar.",
            code="version",
        ) from None

def _version_gate_state() -> dict[str, Any] | None:
    path = _version_gate_file()
    key = _version_gate_key(path)
    with _VERSION_GATE_LOCK:
        stored = _read_version_gate_locked(path)
        observed = _VERSION_GATE_MEMORY.get(key)
        if observed is None:
            return stored
        if stored is not None and _parse_semver_or_none(stored["minVersion"]) >= _parse_semver_or_none(observed["minVersion"]):
            return stored
        return dict(observed)

def _clear_version_gate() -> None:
    path = _version_gate_file()
    key = _version_gate_key(path)
    with _VERSION_GATE_LOCK:
        state = _version_gate_state()
        if state is not None:
            minimum = _parse_semver_or_none(state["minVersion"])
            current = _parse_semver_or_none(_client_app_version(require_reviewed=True))
            if current is None or current < minimum:
                raise AuthError(
                    "A versão instalada ainda não atende ao mínimo exigido. Atualize antes de limpar a trava de versão.",
                    code="version",
                ) from None
        try:
            path.unlink(missing_ok=True)
        except OSError:
            raise AuthError(
                "Não foi possível limpar a trava de versão. O estado anterior foi preservado.", code="version",
            ) from None
        _VERSION_GATE_MEMORY.pop(key, None)

def _maybe_latch_version_gate(text: str, *, clear: bool = False) -> None:
    """Keep an observed minimum in this process even when persistence fails."""
    if clear:
        _clear_version_gate()
        return
    detail = _parse_blocked_detail(text)
    if not detail or detail.get("error") != _APP_VERSION_TOO_OLD:
        return
    minimum = _parse_semver_or_none(detail.get("min_version"))
    if minimum is None:
        return
    path = _version_gate_file()
    key = _version_gate_key(path)
    candidate = {"minVersion": detail["min_version"], "latchedAt": int(time.time())}
    # A diagnostic read may return a version refusal after the phone disconnects.
    # Keep the observed minimum even if the installed version is now unavailable.
    try:
        current_version = _client_app_version()
    except AuthError:
        current_version = None
    # Invalid/unavailable metadata stays a pending in-memory veto. Do not
    # overwrite a durable receipt using an incomplete observation.
    candidate["appVersion"] = current_version
    with _VERSION_GATE_LOCK:
        observed = _VERSION_GATE_MEMORY.get(key)
        if observed is None or minimum > _parse_semver_or_none(observed["minVersion"]):
            _VERSION_GATE_MEMORY[key] = dict(candidate, persistence_pending=True)
        else:
            candidate = {name: value for name, value in observed.items() if name != "persistence_pending"}
            if (_parse_semver_or_none(candidate.get("appVersion")) is None
                    and _parse_semver_or_none(current_version) is not None):
                # Repair only our incomplete in-memory observation, never a
                # corrupt disk receipt or the monotonic maximum minimum.
                candidate["appVersion"] = current_version
                _VERSION_GATE_MEMORY[key] = dict(candidate, persistence_pending=True)
        try:
            stored = _read_version_gate_locked(path)
            if stored is not None and _parse_semver_or_none(stored["minVersion"]) >= _parse_semver_or_none(candidate["minVersion"]):
                _VERSION_GATE_MEMORY[key] = dict(stored)
                return
            # Existing ambiguous/unreadable state is never overwritten to heal it.
            candidate = _validated_version_gate(candidate)
            save_json(path, candidate)
        except (AuthError, OSError, ValueError, UnicodeError):
            # Return the original 403. The authoritative reader will report disk
            # failure later and the observed minimum remains bound to this root.
            _VERSION_GATE_MEMORY[key]["persistence_pending"] = True
            return
        _VERSION_GATE_MEMORY[key] = dict(candidate)


def _semver_tuple(value: str) -> tuple[int, int, int]:
    """Compara versões '1.28.0' de forma tolerante a sufixos."""
    return _parse_semver_or_none(value) or (0, 0, 0)


def _version_gate_blocks() -> bool:
    state = _version_gate_state()
    if state is None:
        return False
    # A refresh may observe another build between the public guard's first
    # probe and this comparison. The very observation used here must be reviewed.
    current = _parse_semver_or_none(_client_app_version(require_reviewed=True))
    if current is None:
        raise AuthError("Não foi possível validar a versão instalada antes de continuar.", code="version") from None
    return current < _parse_semver_or_none(state["minVersion"])


def _client_app_version(*, force_refresh: bool = False, require_reviewed: bool = False) -> str:
    """Resolve the installed phone version, never a remote minimum or env override."""
    from .mobile import minute_version

    try:
        return minute_version.client_version(
            force_refresh=force_refresh, require_reviewed=require_reviewed)
    except minute_version.MinuteVersionError as exc:
        raise AuthError(str(exc), code="version") from None


def _version_update_message(minimum: Any) -> str:
    """Expose only validated version numbers, never a provider response body."""
    try:
        current = _client_app_version()
    except AuthError:
        current = None
    if (_parse_semver_or_none(minimum) is not None
            and _parse_semver_or_none(current) is not None):
        return (f"O serviço exige Minute {minimum}; esta instalação utiliza o protocolo "
                f"{current}. Atualize o cliente antes de iniciar a campanha.")
    return "Versão recusada pelo serviço. Atualize o cliente antes de iniciar a campanha."


def require_supported_client(*, force_refresh: bool = False) -> None:
    """Fail before acquiring media when an observed minimum blocks this client.

    Diagnostic reads remain available. This never clears or relaxes the latch.
    """
    # A new installed build is observed automatically, not assumed compatible.
    # Only locally reviewed pairs may proceed to the ordinary remote policy gates.
    _client_app_version(force_refresh=force_refresh, require_reviewed=True)
    if _version_gate_blocks():
        state = _version_gate_state() or {}
        raise AuthError(_version_update_message(state.get("minVersion")), code="version")


def _check_push_version_binding(method: str, path: str, body: Any, headers: dict[str, str]) -> None:
    """Do not dispatch a materialized push body from before a phone update."""
    if (method.upper() != "PUT"
            or path.split("?", 1)[0].rstrip("/") != "/api/v1/devices/push-token"):
        return
    current = _client_app_version(require_reviewed=True)
    if (not isinstance(body, dict) or body.get("appVersion") != current
            or headers.get("X-App-Version") != current):
        raise AuthError(
            "A versão do Minute mudou antes do registro de push. Atualize a leitura do celular e tente novamente.",
            code="version")


def _normalize_camera_model(model: Any) -> str:
    """`normalizeModel` do APK: trim + lower + colapso de whitespace."""
    if model is None:
        return ""
    return re.sub(r"\s+", " ", str(model).strip().lower())


def _request_detailed(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: Any = None,
    timeout: int = 30,
) -> HttpResponse:
    """Executa HTTP preservando headers como ``X-Blocked-Reason``."""
    request_headers = dict(headers or {})
    if isinstance(body, (dict, list)):
        data = json.dumps(body).encode()
    elif isinstance(body, str):
        data = body.encode()
    else:
        data = None
    if data and not any(
            key.casefold() == "content-type" for key in request_headers):
        request_headers["Content-Type"] = "application/json"
    try:
        status, raw, response_headers = transport.http_request_detailed(
            method, url, headers=request_headers, body=data, timeout=timeout)
        return HttpResponse(
            int(status), raw.decode("utf-8", "replace"),
            {str(k): str(v) for k, v in response_headers.items()},
        )
    except Exception as exc:  # noqa: BLE001 — mesmo contrato de _request
        return HttpResponse(-1, f"ERRO: {exc}", {})


# --- Autenticação ------------------------------------------------------------
def _validate_token_response(data: Any, token_key: str, refresh_key: str,
                             expiry_key: str) -> int:
    """Valida a resposta inteira antes de persistir ou substituir credenciais."""
    if not isinstance(data, dict) or any(
        not isinstance(data.get(key), str) or not data[key].strip()
        for key in (token_key, refresh_key)
    ):
        raise AuthError("Resposta de autenticação inválida.", code="invalid_response")
    value = data.get(expiry_key)
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise AuthError("Validade da sessão inválida.", code="invalid_response")
    try:
        seconds = int(value)
    except ValueError as exc:
        raise AuthError("Validade da sessão inválida.", code="invalid_response") from exc
    if seconds <= 0:
        raise AuthError("Validade da sessão inválida.", code="invalid_response")
    return seconds


def _owned_tokens(data: Any, email: object | None = None, *, uid: str | None = None) -> dict:
    try:
        return token_store.validated(data, email, uid=uid)
    except token_store.TokenStoreError:
        raise AuthError("A identidade do acesso salvo não pôde ser confirmada. Preserve os arquivos e revise o acesso.", code="identity") from None


def _password_login_data(email: str, password: str) -> dict[str, Any]:
    """Validate the provider's demonstrated owner before any token persistence."""
    from .account_bans import require_not_banned
    require_not_banned(email)
    response = _request_detailed(
        _SIGNIN_URL,
        "POST",
        body={"email": email, "password": password, "returnSecureToken": True},
    )
    status, body = response.status, response.text
    if status != 200:
        raise _auth_failure(status, body, "Login", firebase=True,
                            headers=response.headers)
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AuthError("Login devolveu resposta inválida.", code="invalid_response") from exc

    expires_in = _validate_token_response(data, "idToken", "refreshToken", "expiresIn")
    data = _owned_tokens(data, email)
    data["expires_at"] = _expiry_from_token(
        data["idToken"], expires_in
    )
    return data


def login(email: str, password: str) -> dict[str, Any]:
    """Autentica e grava apenas o primário hash da identidade demonstrada."""
    try:
        email = token_store.email_key(email)
    except token_store.TokenStoreError:
        raise AuthError("O acesso primário está inválido ou conflitante; preserve-o antes de reconectar.", code="identity") from None
    with _lock_for(token_store.record_path(config.SECRETS_DIR, email)):
        try:
            token_store.preflight_write(config.SECRETS_DIR, email)
        except token_store.TokenStoreError:
            raise AuthError("O acesso primário está inválido ou conflitante; preserve-o antes de reconectar.", code="identity") from None
        data = _password_login_data(email, password)
        try:
            token_store.save(config.SECRETS_DIR, email, data)
        except token_store.TokenStoreError:
            raise AuthError("A identidade do acesso não pôde ser salva com segurança; os registros anteriores foram preservados.", code="identity") from None
        return data


def register_identity(email: str, password: str, code: str | None = None) -> None:
    """Confirma somente a criação remota Minute, sem autenticar nem salvar token.

    A separação deixa o chamador persistir o checkpoint do POST antes do login,
    que é uma operação distinta e pode falhar depois da criação confirmada.
    """
    from .account_bans import require_not_banned
    from .org_policy import target_invite
    try:
        email = token_store.email_key(email)
        token_store.preflight_write(config.SECRETS_DIR, email)
    except token_store.TokenStoreError:
        raise AuthError("O acesso primário está inválido ou conflitante; preserve-o antes de registrar.", code="identity") from None
    require_not_banned(email)
    expected_code = target_invite(email)
    effective_code = str(code or expected_code).strip().upper()
    if effective_code != expected_code:
        raise ValueError(
            f"Código Minute recusado para esta conta; use somente {expected_code}."
        )
    profile = device_profile.get_profile(email)
    headers = profile.headers(include_location=False)
    response = _request_detailed(
        config.BASE_URL + "/api/v1/auth/web-register",
        "POST",
        headers=headers,
        body={
            "email": email,
            "password": password,
            "code": effective_code,
            "device_id": profile.device_id,
        },
    )
    status, body = response.status, response.text
    if status in (429, 408, -1) or status >= 500:
        error = _auth_failure(status, body, "Registro Minute",
                              headers=response.headers)
        # Explicit HTTP rejections are not admission. A transport loss,
        # timeout or server failure after POST may follow a remote creation.
        error.remote_effect_possible = status in (-1, 408) or status >= 500
        raise error
    if status not in (200, 201):
        duplicate = _minute_duplicate_marker(body)
        if duplicate is not None:
            error = AuthError(f"Registro Minute: {duplicate} (HTTP {status}).",
                              code="duplicate", http_status=status)
        else:
            error = _auth_failure(status, body, "Registro Minute",
                                  headers=response.headers)
            if status not in (401, 403, 429):
                error.account_issue_code = "invalid_response"
        error.remote_effect_possible = 200 <= status < 300
        raise error


def register(email: str, password: str, code: str | None = None) -> dict[str, Any]:
    """Registra uma nova conta e autentica para persistir o acesso (legado)."""
    register_identity(email, password, code)
    return login(email, password)


def _jwt_exp(token: str | None) -> int:
    """`exp` do JWT (epoch s), sem verificar assinatura. 0 se não for JWT."""
    if not token or token.count(".") < 2:
        return 0
    try:
        payload = token.split(".")[1]
        payload += "=" * ((-len(payload)) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        return int(data.get("exp") or 0)
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeError):
        return 0


def _expiry_from_token(id_token: str | None, expires_in: int = 3600) -> int:
    """Prefere o `exp` do JWT (relógio do Firebase) ao relógio local + expiresIn."""
    jwt_exp = _jwt_exp(id_token)
    if jwt_exp > 0:
        return jwt_exp
    return int(time.time()) + int(expires_in)


def _file_expires_at(data: dict[str, Any]) -> int:
    """expires_at do arquivo em epoch s. Aceita milissegundos por engano."""
    raw = data.get("expires_at") or 0
    try:
        ts = int(float(raw))
    except (TypeError, ValueError):
        return 0
    if ts > 10_000_000_000:  # milissegundos
        ts //= 1000
    return ts


def _token_expiry(data: dict[str, Any]) -> int:
    """Epoch em segundos. 0 = desconhecido (tratar como vencido).

    Fonte de verdade: o MENOR entre `expires_at` do arquivo e o `exp` do JWT.
    O arquivo usa relógio local + expiresIn (~21s de skew vs Firebase). O
    Minute valida o JWT no relógio do Google — JWT morto com expires_at no
    futuro era o 401 `Invalid Firebase ID token`.
    """
    file_exp = _file_expires_at(data)
    token = data.get("idToken") or data.get("id_token") or ""
    jwt_exp = _jwt_exp(token)
    candidates = [t for t in (file_exp, jwt_exp) if t > 0]
    if not candidates:
        return 0
    return min(candidates)


def _lock_for(path: Path | None) -> threading.Lock:
    """Um lock por arquivo de token — o refreshToken do Firebase rotaciona."""
    key = str(path.resolve()) if path else ""
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def _lookup_password(email: str | None) -> str | None:
    """Senha salva da conta (contas.jsonl + crowtado_passwords.json)."""
    if not email or "@" not in email:
        return None
    try:
        saved = credential_store.lookup(config.SECRETS_DIR, email, strict=True)
    except ValueError:
        # Um registro individual existe, mas perdeu integridade. Não use uma
        # cópia legada potencialmente antiga para autenticar silenciosamente.
        return None
    if saved:
        return saved
    found: str | None = None
    contas = config.DATA_DIR / "contas.jsonl"
    try:
        for line in contas.read_text(encoding="utf-8-sig").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(rec.get("email") or "").strip().casefold() == email.strip().casefold() and rec.get("senha"):
                found = str(rec["senha"])
    except OSError:
        pass
    pw_path = config.SECRETS_DIR / "crowtado_passwords.json"
    try:
        creds = json.loads(pw_path.read_text(encoding="utf-8-sig"))
        if isinstance(creds, dict):
            for key, value in creds.items():
                if str(key).strip().casefold() == email.strip().casefold() and isinstance(value, str) and value:
                    found = value
    except (OSError, json.JSONDecodeError):
        pass
    return found or None


def _refresh(token_data: dict[str, Any]) -> dict[str, Any]:
    """Renova o idToken usando o refreshToken. Devolve o dict atualizado.

    Levanta AuthError se não houver refreshToken ou se o Firebase recusar
    (refresh token expirado/revogado) — nesse caso é preciso refazer o login.
    """
    token_data = _owned_tokens(token_data)
    original_uid = token_store.identity_uid(token_data)
    refresh_token = token_data.get("refreshToken") or token_data.get("refresh_token")
    if not refresh_token:
        raise AuthError("sem refreshToken no arquivo — reconecte o acesso.", code="authentication")
    status, body = _request(
        _REFRESH_URL,
        "POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=urlencode({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }),
        raw_form=True,
    )
    if status != 200:
        raise _auth_failure(status, body, "Renovação da sessão", firebase=True)
    try:
        resp = json.loads(body) if (body or "").strip() else None
    except json.JSONDecodeError as exc:
        raise AuthError(
            f"refresh devolveu resposta vazia/não-JSON ({status}).", code="invalid_response",
        ) from exc
    expires_in = _validate_token_response(resp, "id_token", "refresh_token", "expires_in")
    try:
        returned_uid = token_store.identity_uid(resp)
        if original_uid is not None and returned_uid != original_uid:
            raise token_store.TokenStoreError
    except token_store.TokenStoreError:
        raise AuthError("A renovação não confirmou a mesma identidade. O acesso anterior foi preservado.", code="identity") from None
    expires_at = _expiry_from_token(resp["id_token"], expires_in)
    token_data.update(idToken=resp["id_token"], refreshToken=resp["refresh_token"],
                      expiresIn=str(expires_in), expires_at=expires_at)
    if original_uid is None and returned_uid is not None:
        token_data["user_id"] = returned_uid
    return token_data


def _align_physical_device_headers(headers: dict[str, str]) -> None:
    """No modo device, headers de aparelho vêm só do celular físico conectado.

    ``User-Agent`` permanece o literal OkHttp do APK (igual em todos os
    aparelhos). ``X-Device-Location`` só entra com amostra GPS do telefone;
    coordenadas de perfil/env nunca são reutilizadas em modo device.
    """
    from .mobile import physical_binding as pb
    from .mobile.physical_binding import PhysicalDeviceRequiredError
    from . import roglife_appcheck

    if not roglife_appcheck.device_mode_enabled():
        return
    try:
        bound = pb.require_campaign_physical_binding()
    except PhysicalDeviceRequiredError as exc:
        raise AuthError(str(exc), code="device") from exc
    headers["X-Device-Id"] = bound["device_id"]
    headers["X-App-Version"] = bound["app_version"]
    headers["User-Agent"] = bound["user_agent"]
    location = bound.get("location_header")
    if isinstance(location, str) and location.strip():
        headers["X-Device-Location"] = location.strip()
    else:
        headers.pop("X-Device-Location", None)


# --- Sessão ------------------------------------------------------------------
class Session:
    """Sessão autenticada com refresh automático e persistência do token."""

    def __init__(self, token_data: dict[str, Any], token_file: Path | None = None,
                 email: str | None = None):
        token_data = _owned_tokens(token_data, email)
        if "idToken" not in token_data and "id_token" in token_data:
            token_data["idToken"] = token_data["id_token"]
        self.data = token_data
        self.token_file = Path(token_file) if token_file else None
        if self.token_file:
            try:
                token_store.validate_path(self.token_file, token_data["email"])
            except token_store.TokenStoreError:
                raise AuthError("O caminho primário não corresponde à identidade demonstrada.", code="identity") from None
        # e-mail da conta (para o perfil de aparelho: X-Device-Id, UA, uptime)
        self.email = token_store.email_key(token_data["email"])
        self._identity_email = self.email
        self._identity_uid = token_store.identity_uid(token_data)
        # False até um refresh/login nesta instância — o idToken do disco
        # nunca é enviado à API sem troca no Firebase.
        self._live = False
        self._lock = threading.RLock()
        self.recording_policy: RecordingPolicy | None = None
        self.initialization_errors: dict[str, str] = {}
        self._initialization_causes: dict[str, AuthError] = {}
        self._recording_checked_at: float | None = None
        self._recording_retry_at = 0.0
        self._quota_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._quota_failures: dict[str, AuthError] = {}

    @classmethod
    def from_file(cls, token_file: str | Path, *, live: bool = True) -> Session:
        """Carrega o JSON e, por padrão, troca o idToken no Firebase na hora.

        O arquivo só guarda o refreshToken de forma durável. Mandar o idToken
        estático do disco era o 401 `Invalid Firebase ID token` (exp do JWT
        já morto, token revogado, expires_at em ms). `live=False` só para
        testes que não falam com a rede.
        """
        path = Path(token_file)
        try:
            data = token_store.read_file(path)
        except token_store.TokenStoreError:
            raise AuthError("O acesso salvo está inválido ou sem identidade demonstrada. Preserve o arquivo e revise o acesso.", code="identity") from None
        sess = cls(data, path)
        if live:
            sess.refresh()
        return sess

    @classmethod
    def from_email(cls, email: str, *, live: bool = True) -> Session:
        try:
            found = token_store.load(config.SECRETS_DIR, email)
        except token_store.TokenStoreError:
            raise AuthError("O acesso salvo está inválido ou possui identidade conflitante. Os arquivos foram preservados.", code="identity") from None
        if found is None:
            raise AuthError(
                f"nenhum acesso salvo para {email}; adicione ou reautentique "
                "a conta pela aba Contas do QMoney"
            )
        path, data = found
        sess = cls(data, path, email=email)
        if live:
            sess.refresh()
        return sess

    def with_email(self, email: str) -> Session:
        """Fixa o e-mail da conta (fonte do perfil de aparelho)."""
        with self._lock:
            self._check_identity()
            _owned_tokens(self.data, email, uid=self._identity_uid)
            if self._identity_email != token_store.email_key(email):
                raise AuthError("A sessão não pode ser vinculada a outra identidade.", code="identity") from None
            self.email = token_store.email_key(email)
        return self

    def _who(self) -> str:
        return self.email or self.data.get("email") or "esta conta"

    def _bearer(self) -> str:
        self._check_identity()
        return str(self.data.get("idToken") or self.data.get("id_token") or "")

    def _check_identity(self) -> None:
        _owned_tokens(self.data, self._identity_email, uid=self._identity_uid)
        try:
            if token_store.email_key(self.email) != self._identity_email:
                raise token_store.TokenStoreError("identity")
        except token_store.TokenStoreError:
            raise AuthError("A sessão não confirmou a identidade original.", code="identity") from None

    @property
    def id_token(self) -> str:
        """idToken vivo. Troca no Firebase se esta instância ainda não
        renovou ou se falta menos de 10 min para o exp do JWT."""
        remaining = _token_expiry(self.data) - time.time()
        if (not getattr(self, "_refreshing", False)
                and (not self._live or remaining <= _REFRESH_SKEW_S)):
            self.refresh()
        token = self._bearer()
        if not token:
            raise AuthError(
                f"token vazio para {self._who()} — refaça o login "
                "pela aba Contas do QMoney."
            )
        return token

    def _reload_from_disk(self) -> None:
        """Outra thread pode ter rotacionado o refreshToken no mesmo arquivo."""
        self._check_identity()
        if not self.token_file or not (self.token_file.exists() or self.token_file.is_symlink()):
            return
        try:
            disk = token_store.read_file(self.token_file, self._identity_email, uid=self._identity_uid)
        except token_store.TokenStoreError:
            raise AuthError("O acesso em disco não confirmou a identidade da sessão. Memória e arquivo foram preservados.", code="identity") from None
        if disk.get("refreshToken") or disk.get("refresh_token"):
            if "idToken" not in disk and "id_token" in disk:
                disk["idToken"] = disk["id_token"]
            self.data = disk
            if not self.email:
                self.email = disk.get("email")

    def _persist(self, candidate: dict | None = None) -> None:
        self._check_identity()
        candidate = _owned_tokens(self.data if candidate is None else candidate, self._identity_email,
                                  uid=self._identity_uid)
        if self.token_file:
            try:
                token_store.save_file(self.token_file, candidate, self.email,
                                      uid=self._identity_uid)
            except token_store.TokenStoreError:
                raise AuthError("O acesso não pôde ser persistido com a mesma identidade. O arquivo anterior foi preservado.", code="identity") from None

    # -- chamadas genéricas --------------------------------------------------
    def request(self, method: str, path: str, body: Any = None) -> tuple[int, str]:
        self._check_write_policy(method, path, body)
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self.id_token}",
            "Accept": "*/*",
            "Accept-Language": config.ACCEPT_LANGUAGE,
        }
        # Location remains absent without a configured valid sample.
        if self.email:
            headers.update(device_profile.get_profile(self.email).headers(
                include_location=_is_geo_route(path, method)))
        else:
            headers["X-App-Version"] = config.APP_VERSION
            headers["User-Agent"] = config.USER_AGENT
        # Escritas POST/PUT/PATCH exigem App Check com a fase da operação.
        from . import appcheck
        headers.update(require_app_check_for_write(method, path))
        _align_physical_device_headers(headers)
        self._check_write_policy(method, path, body)
        headers["Authorization"] = f"Bearer {self._bearer()}"
        _check_push_version_binding(method, path, body, headers)
        status, text = _request(config.BASE_URL + path, method, headers=headers, body=body)
        if status == 403:
            # Kill-switch de versão mínima: o app trava qualquer 403 que pareça
            # version gate (maybeLatchVersionGate); aqui persistem localmente.
            _maybe_latch_version_gate(text)
        if status != 401 or appcheck.is_app_check_rejection(text) or getattr(self, "_refreshing", False):
            return status, text
        # authedFetch do APK 1.29.0 repete uma vez após renovar o Bearer.
        # O segundo 401 permanece uma rejeição; não autoriza outro pedido.
        # The first Minute request was already dispatched; auth failures from
        # here on cannot claim request_not_sent for that operation.
        self._refreshing = True
        try:
            try:
                self.refresh()
            except AuthError as exc:
                exc.remote_effect_possible = True
                raise
            try:
                self._check_write_policy(method, path, body)
                headers["Authorization"] = f"Bearer {self._bearer()}"
                _check_push_version_binding(method, path, body, headers)
            except AuthError as exc:
                exc.remote_effect_possible = True
                raise
            status2, text2 = _request(
                config.BASE_URL + path, method, headers=headers, body=body)
            if status2 == 403:
                _maybe_latch_version_gate(text2)
            return status2, text2
        finally:
            self._refreshing = False

    def request_detailed(
        self, method: str, path: str, body: Any = None,
    ) -> HttpResponse:
        """Como :meth:`request`, incluindo headers e o mesmo refresh de token."""
        self._check_write_policy(method, path, body)
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self.id_token}",
            "Accept": "*/*",
            "Accept-Language": config.ACCEPT_LANGUAGE,
        }
        if self.email:
            headers.update(device_profile.get_profile(self.email).headers(
                include_location=_is_geo_route(path, method)))
        else:
            headers["X-App-Version"] = config.APP_VERSION
            headers["User-Agent"] = config.USER_AGENT
        # Escritas POST/PUT/PATCH exigem App Check com a fase da operação.
        from . import appcheck
        headers.update(require_app_check_for_write(method, path))
        _align_physical_device_headers(headers)

        self._check_write_policy(method, path, body)
        headers["Authorization"] = f"Bearer {self._bearer()}"
        _check_push_version_binding(method, path, body, headers)
        response = _request_detailed(
            config.BASE_URL + path, method, headers=headers, body=body)
        if response.status == 403:
            _maybe_latch_version_gate(response.text)
        if (response.status != 401 or appcheck.is_app_check_rejection(response.text)
                or any(key.casefold() == "x-blocked-reason"
                       and appcheck.is_app_check_blocked_reason(value)
                       for key, value in response.headers.items())
                or getattr(self, "_refreshing", False)):
            return response
        # First Minute request already dispatched; later AuthError is uncertain.
        self._refreshing = True
        try:
            try:
                self.refresh()
            except AuthError as exc:
                exc.remote_effect_possible = True
                raise
            try:
                self._check_write_policy(method, path, body)
                headers["Authorization"] = f"Bearer {self._bearer()}"
                _check_push_version_binding(method, path, body, headers)
            except AuthError as exc:
                exc.remote_effect_possible = True
                raise
            refreshed = _request_detailed(
                config.BASE_URL + path, method, headers=headers, body=body)
            if refreshed.status == 403:
                _maybe_latch_version_gate(refreshed.text)
            return refreshed
        finally:
            self._refreshing = False

    def get(self, path: str) -> tuple[int, str]:
        return self.request("GET", path)

    def post(self, path: str, body: Any = None) -> tuple[int, str]:
        return self.request("POST", path, body)

    def register_push_token(self, token: str, locale: str = "pt") -> tuple[int, str]:
        """PUT /api/v1/devices/push-token — vincula o FCM token ao device da conta.

        O app real envia depois do sign-in e quando o FCM rotaciona; o
        X-Device-Id vai junto (mesmo header do request_detailed) e vincula o
        token ao aparelho do perfil. Idempotente no servidor.
        """
        if not self.email:
            return -1, "ERRO: sessão sem e-mail (perfil de aparelho ausente)"
        response = self.request_detailed(
            "PUT", "/api/v1/devices/push-token",
            body={"token": token, "locale": locale, "appVersion": _client_app_version()})
        return response.status, response.text

    def json(self, method: str, path: str, body: Any = None) -> Any:
        """Como request(), mas devolve o corpo já parseado (ou {} se não-JSON)."""
        status, text = self.request(method, path, body)
        if not 200 <= status < 300:
            return {}
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return {}

    # -- atalhos de domínio --------------------------------------------------
    def me(self) -> Any:
        return self.json("GET", "/api/v1/users/me")

    def organizations(self) -> Any:
        return self.json("GET", "/api/v1/organizations")

    def _catalog_json(self, path: str) -> Any:
        """A denied or malformed catalog must never look like an empty catalog."""
        status, text = self.get(path)
        if not 200 <= status < 300:
            raise _auth_failure(status, text, "Consulta do catálogo")
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            raise AuthError("Catálogo retornou conteúdo inválido.", code="invalid_response") from None
        if isinstance(data, dict):
            rows = next((data[k] for k in ("tasks", "items", "data", "results", "categories") if k in data), None)
        else:
            rows = data
        if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
            raise AuthError("Catálogo retornou estrutura inválida.", code="invalid_response")
        return data

    def categories(self, org_key: str | None = None) -> Any:
        """Categorias para enquadramento a partir das tasks da org (Minute 1.29).

        O app 1.29 não usa `/api/v1/categories` como fonte da home; as
        categorias vêm embutidas em cada task. Se `org_key` for omitido e o
        usuário tiver exatamente uma org, usa essa; caso contrário exige a chave.
        """
        key = org_key
        if not key:
            orgs = self.my_orgs()
            if len(orgs) == 1:
                key = orgs[0].get("resourceKey") or orgs[0].get("key") or orgs[0].get("id")
            else:
                raise AuthError(
                    "categories() no 1.29 exige org_key (categorias vêm das tasks).",
                    code="invalid_request",
                )
        tasks = self.all_tasks(str(key))
        return categories_from_tasks(tasks)

    def org_tasks(self, org_key: str) -> Any:
        query = _tasks_lang_query()
        return self._catalog_json(f"/api/v1/orgs/{org_key}/tasks{query}")

    def org_quota(self, org_key: str) -> Any:
        """Quota/geo da org (`getRecordingGeo` do app)."""
        return self.recording_geo(org_key)

    def recording_geo(self, org_key: str) -> dict[str, Any]:
        """GET /orgs/{org}/quota — autorização de gravação/geo com cache curto.

        Contrato OpenAPI `QuotaStatusResponse`. Resposta inválida ou HTTP de
        erro devolve `{}` (estado desconhecido — não autoriza upload). A causa
        sanitizada fica vinculada à organização para o gate de autorização;
        uma recusa de versão nunca vira um diagnóstico genérico de quota.
        """
        cached = self._quota_cache.get(org_key)
        now = time.monotonic()
        if cached is not None and now - cached[0] < 60:
            return dict(cached[1])
        self._quota_failures.pop(org_key, None)
        response = self.request_detailed("GET", f"/api/v1/orgs/{org_key}/quota")
        if response.status != 200:
            self._quota_failures[org_key] = _auth_failure(
                response.status, response.text, "Consulta de quota/geografia",
                headers=response.headers)
            return {}

        def invalid_response() -> dict[str, Any]:
            self._quota_failures[org_key] = AuthError(
                "Consulta de quota/geografia: resposta HTTP 200 com autorização inválida ou incompleta.",
                code="invalid_response", http_status=200)
            return {}

        try:
            data = json.loads(response.text)
        except (ValueError, TypeError):
            return invalid_response()
        if not isinstance(data, dict):
            return invalid_response()
        auth = data.get("recordingAuthorization")
        can = data.get("canUpload")
        reason = data.get("blockedReason")
        if auth not in ("APPROVED", "REQUIRES_LOCATION", "BLOCKED"):
            return invalid_response()
        if type(can) is not bool or "blockedReason" not in data:
            return invalid_response()
        if reason is not None and reason not in (
                "manual", "quota_exceeded", "geo_restricted", "geo_unknown"):
            return invalid_response()
        self._quota_cache[org_key] = (now, data)
        return dict(data)

    def check_recording_authorization(self, org_key: str) -> None:
        """Read-only admission gate shared by campaign review/start and CREATE.

        An approved review is not a promise that a later upload will be accepted.
        The write boundary still revalidates its own policy and authorization.
        """
        require_supported_client()
        state = self.org_state(org_key)
        if state["blocked"]:
            account_disabled = state.get("account_disabled") is True
            raise AuthError("A organização ou a conta não permite novos envios.",
                            code="restricted" if account_disabled else "access_paused",
                            provider_error_code="user_account_disabled" if account_disabled else None)
        if state["userState"] != "active":
            raise AuthError("Estado de qualidade Minute não verificado.", code="invalid_response")
        self._check_recording_geo(org_key)

    def join_org(self, code: str) -> tuple[int, str]:
        return self.post("/api/v1/organizations/join", {"code": code})

    def create_org(self, name: str, include_default_tasks: bool = True) -> tuple[int, str]:
        return self.post(
            "/api/v1/organizations",
            {"name": name, "include_default_tasks": include_default_tasks},
        )

    # -- gates do app (qualidade/versão/dispositivo) -------------------------
    def version_gate(self) -> dict[str, Any] | None:
        """Read the effective root-bound latch; corrupt state is visible."""
        return _version_gate_state()

    def clear_version_gate(self) -> None:
        """Clear only after the installed version satisfies the known minimum."""
        _clear_version_gate()

    def quality_state(self, org_key: str) -> dict[str, Any]:
        """Estado de qualidade do próprio usuário autenticado.

        Leitura compatível: falhas não autorizam acesso e devolvem ``{}``.
        Gates de acesso devem usar ``checked_quality_state``.
        """
        status, text = self.get(f"/api/v1/organizations/{org_key}/quality-summary")
        if status != 200:
            return {}
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    def checked_quality_state(self, org_key: str) -> dict[str, Any]:
        """A verification must distinguish a failed quality read from active access."""
        response = self.request_detailed(
            "GET", f"/api/v1/organizations/{org_key}/quality-summary")
        if response.status != 200:
            raise _auth_failure(response.status, response.text, "Consulta de restrições Minute",
                                headers=response.headers)
        try:
            state = json.loads(response.text)
        except (ValueError, TypeError):
            raise AuthError("Estado de qualidade Minute inválido", code="invalid_response") from None
        if not isinstance(state, dict) or state.get("userState") not in ("active", "on_hold", "inactive"):
            raise AuthError("Estado de qualidade Minute incompleto", code="invalid_response")
        return state

    def org_state(self, org_key: str) -> dict[str, Any]:
        """Estado operacional do usuário numa org (disabled + userState).

        Fontes (contrato observado no APK Minute 1.30):
          - /users/me → {disabled (conta), organizations[]}
          - /organizations/{org}/quality-summary → userState

        `blocked=True` só para a ORG ALVO desativada ou userState
        on_hold/inactive — uma org desativada entre várias NÃO bloqueia.
        """
        profile = self.me() or {}
        if not isinstance(profile, dict) or not isinstance(profile.get("organizations"), list):
            raise AuthError("Não foi possível verificar o perfil da organização.", code="invalid_response")
        org = next(
            (o for o in (profile.get("organizations") or [])
             if isinstance(o, dict) and o.get("resourceKey") == org_key),
            None)
        account_disabled = profile.get("disabled") is True
        organization_disabled = isinstance(org, dict) and org.get("disabled") is True
        disabled = account_disabled or organization_disabled
        summary: dict[str, Any] = {}
        # Uma restrição já confirmada tem prioridade sobre a disponibilidade
        # da qualidade. Sem disabled, uma consulta falha deve preservar a causa.
        if not disabled:
            summary = self.checked_quality_state(org_key)
        user_state = summary.get("userState", "unknown")
        return {
            "org_key": org_key,
            "disabled": disabled,
            "account_disabled": account_disabled,
            "organization_disabled": organization_disabled,
            "userState": user_state,
            "overall": summary.get("overallBandCode", summary.get("overall")),
            "blocked": disabled or user_state in ("on_hold", "inactive"),
            "cameraSources": (org or {}).get("cameraSources") or [],
        }

    def camera_policy(self) -> dict[str, Any]:
        """GET /devices/native-camera-policy — contrato OpenAPI NativeCameraPolicyResponse."""
        status, text = self.get("/api/v1/devices/native-camera-policy")
        if status != 200:
            return {}
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return {}
        if (not isinstance(data, dict) or type(data.get("policyVersion")) is not int
                or data["policyVersion"] < 1):
            return {}
        for key in ("iosAllowModels", "iosDeniedModels",
                    "androidAllowModels", "androidAllowModelPatterns"):
            if not isinstance(data.get(key), list) or any(not isinstance(item, str) for item in data[key]):
                return {}
        normalization = data.get("normalization")
        if not isinstance(normalization, dict) or any(type(normalization.get(key)) is not bool for key in
                ("trimWhitespace", "lowercase", "collapseInternalWhitespace")):
            return {}
        return data

    def camera_model_allowed(self, model: str, *, platform: str = "android") -> bool | None:
        """Implementação local de política de câmera derivada da referência histórica.

        Sempre normaliza o modelo (trim + lower + colapso de espaços), compara
        com `androidAllowModels` crus e aplica `RegExp(pattern).test(model)`
        via `re.search`. Não há deny-list Android no contrato OpenAPI/Hermes.
        iOS usa as listas exatas de modelos, com a negativa tendo precedência.
        """
        policy = self.camera_policy()
        if not policy:
            return None
        normalized = _normalize_camera_model(model)
        if platform == "ios":
            if not normalized or normalized in policy["iosDeniedModels"]:
                return False
            return normalized in policy["iosAllowModels"]
        if platform != "android":
            return False
        allowed = policy.get("androidAllowModels") or []
        if normalized in allowed:
            return True
        for pattern in (policy.get("androidAllowModelPatterns") or []):
            try:
                if re.search(str(pattern), normalized) is not None:
                    return True
            except re.error:
                continue
        return False

    # -- autenticação / robustez --------------------------------------------
    def _relogin(self) -> Session:
        """Login com senha salva. Usado quando o refreshToken não convence o Minute."""
        email = self._who()
        lock = _lock_for(self.token_file) if self.token_file else self._lock
        with lock:
            self._check_identity()
            if self.token_file and (self.token_file.exists() or self.token_file.is_symlink()):
                try:
                    token_store.read_file(self.token_file, email, uid=self._identity_uid)
                except token_store.TokenStoreError:
                    raise AuthError("O acesso em disco não confirmou a mesma identidade. Preserve os registros.", code="identity") from None
            password = _lookup_password(email)
            if not password:
                raise AuthError(
                    f"{email}: sem senha salva para re-login "
                    "(secrets/crowtado_passwords.json).", code="missing_access",
                )
            try:
                candidate = _password_login_data(email, password)
            except AuthError:
                raise
            except RuntimeError as login_exc:
                raise AuthError(
                    f"{email}: login com senha salva falhou: {login_exc}"
                ) from login_exc
            self._persist(candidate)
            self.data = candidate
            self._identity_uid = self._identity_uid or token_store.identity_uid(candidate)
            self._live = True
        return self

    def refresh(self) -> Session:
        """Troca o idToken no Firebase e persiste. Sempre dinâmico.

        1. Recarrega o arquivo (refreshToken pode ter rotacionado).
        2. POST securetoken.googleapis.com com o refreshToken.
        3. Se o refresh falhar, tenta login com a senha salva da conta.
        """
        email = self._who()
        lock = _lock_for(self.token_file) if self.token_file else self._lock
        with lock:
            previous_data, previous_live = self.data, self._live
            try:
                self._reload_from_disk()
                try:
                    candidate = _refresh(self.data)
                except AuthError as exc:
                    if exc.account_issue_code and exc.account_issue_code != "authentication":
                        raise
                    try:
                        return self._relogin()
                    except AuthError as login_exc:
                        raise login_exc from exc
                self._persist(candidate)
                self.data = candidate
                self._identity_uid = self._identity_uid or token_store.identity_uid(candidate)
                self._live = True
            except Exception:
                self.data, self._live = previous_data, previous_live
                raise
        return self

    def ensure_auth(self, *, org_key: str | None = None) -> dict[str, Any]:
        """Garante que a sessão está válida chamando /users/me.

        Sempre troca o idToken no Firebase antes da 1ª chamada desta
        instância — o JWT estático do disco não é enviado. Um HTTP 200 não
        basta: o HUB mantém o perfil consultável mesmo quando a conta foi
        desativada, mas bloqueia sessão/upload com 403. Detectar ``disabled``
        aqui evita iniciar uma campanha que falharia em cada clipe.

        Com `org_key`, replica as travas do app para aquela org:
          - `quality-summary` (userState on_hold/inactive) → conta parada;

        O latch de versão é aplicado às mutações, preservando esta leitura
        para diagnóstico.

        Levanta AuthError com instrução clara se a conta não autenticar (ex.:
        refresh token expirado), estiver desativada ou em hold.
        """
        if not self._live:
            self.refresh()
        response = self.request_detailed("GET", "/api/v1/users/me")
        status, body = response.status, response.text
        email = self._who()
        if status == 200:
            try:
                profile = json.loads(body)
            except (json.JSONDecodeError, ValueError) as exc:
                raise AuthError(
                    f"resposta inválida ao validar {email}", code="invalid_response",
                ) from exc
            if not isinstance(profile, dict) or not isinstance(profile.get("organizations"), list):
                raise AuthError("O perfil devolvido pelo serviço está incompleto.", code="invalid_response")
            if profile.get("disabled") is True:
                raise AuthError(
                    "Conta desativada no HUB. A plataforma precisa reativá-la antes de novos envios.",
                    code="restricted", provider_error_code="user_account_disabled")
            # A autorização de mutações é verificada no write path.
            # Bloqueio POR ORG ALVO (disabled da conta/org + userState). Uma org
            # desativada entre várias NÃO bloqueia a conta inteira — só a org
            # para a qual o envio realmente vai.
            if org_key:
                state = self.org_state(org_key)
                if state["blocked"]:
                    what = ("org desativada" if state["disabled"]
                            else f"conta {state['userState']}"
                            if state["userState"] in ("on_hold", "inactive")
                            else "indisponível")
                    code = ("restricted" if state["disabled"] and profile.get("disabled") is True
                           else "access_paused")
                    raise AuthError(
                        f"{email}: {what} para {org_key} — a plataforma não "
                        "aceita envios agora (o app Minute pararia aqui).", code=code,
                    )
                if state["userState"] != "active":
                    raise AuthError("Estado de qualidade Minute não verificado.", code="invalid_response")
            self.warmup()
            return profile
        raise _auth_failure(status, body, "Consulta do perfil", headers=response.headers,
                            profile_read=True)

    # -- orgs / tasks (respostas normalizadas) -------------------------------
    def my_orgs(self) -> list[dict[str, Any]]:
        """Organizações do usuário (a partir de /users/me)."""
        return (self.me() or {}).get("organizations") or []

    def all_tasks(self, org_key: str) -> list[dict[str, Any]]:
        """Tasks com nomes canônicos para seleção; a interface traduz os rótulos."""
        query = _tasks_lang_query(canonical=True)
        body = self._catalog_json(f"/api/v1/orgs/{org_key}/tasks{query}")
        return validate_task_catalog(_as_list(body))

    # -- telemetria (comportamento de app aberto) ----------------------------
    def app_opened(self, auth_method: str = "SESSION_RESUMED",
                   opened_at: str | None = None) -> tuple[int, str]:
        """POST /api/v1/app/opened — telemetria de abertura do app.

        Em modo device o corpo (app_version/device_model/os_version) vem do
        celular físico conectado. Fora do modo device usa o perfil da conta.
        """
        from .mobile import physical_binding as pb
        from .mobile.physical_binding import PhysicalDeviceRequiredError

        if pb.device_mode_requires_physical():
            try:
                bound = pb.require_campaign_physical_binding()
            except PhysicalDeviceRequiredError as exc:
                raise AuthError(str(exc), code="device") from exc
            opened = bound["opened"]
            body: dict[str, Any] = {
                "auth_method": auth_method,
                "app_version": opened["app_version"],
                "device_model": opened["device_model"],
                "os_version": opened["os_version"],
            }
            if opened_at:
                body["opened_at"] = opened_at
            return self.post("/api/v1/app/opened", body)
        if not self.email:
            return -1, "ERRO: sessão sem e-mail (perfil de aparelho ausente)"
        profile = device_profile.get_profile(self.email)
        return self.post("/api/v1/app/opened",
                         profile.opened_payload(auth_method, opened_at))

    def fetch_recording_config(self) -> tuple[int, str]:
        """GET /devices/recording-config — o app consulta ao abrir/gravar.

        A resposta é validada por RecordingPolicy e mantida nesta sessão.
        Falhas de consulta impedem o registro de novos uploads até recuperação.
        """
        return self.get("/api/v1/devices/recording-config")

    def _check_local_restrictions(self) -> None:
        from . import vpn
        if vpn.ENFORCE:
            active = vpn.vpn_active()
            if active is None:
                raise AuthError("Não foi possível verificar a política de VPN. Tente novamente.", code="service")
            if active:
                raise AuthError("Operação bloqueada pela política de VPN configurada.", code="policy")
        if config.REQUIRE_CURL and transport.kind() == "urllib":
            raise AuthError("Transporte obrigatório indisponível.", code="service")

    def _check_write_policy(self, method: str, path: str, body: Any) -> None:
        self._check_local_restrictions()
        method_u = method.upper()
        # Latch de versão é app-wide no APK; leituras seguem liberadas p/ diagnóstico.
        if method_u in ("POST", "PUT", "PATCH", "DELETE"):
            require_supported_client()
        route = path.split("?", 1)[0].rstrip("/")
        if method_u != "POST" or route != "/api/v1/uploads":
            return
        self.warmup()
        if self.recording_policy is None or "recording_config" in self.initialization_errors:
            cause = self._initialization_causes.get("recording_config")
            if cause is not None:
                raise cause
            raise AuthError("Não foi possível validar os limites desta sessão. Tente novamente.", code="service")
        meta = body.get("meta") if isinstance(body, dict) else None
        platform = meta.get("platform") if isinstance(meta, dict) else None
        source_os = platform.get("os", platform.get("type")) if isinstance(platform, dict) else None
        is_ios = source_os == "ios"
        allowed = getattr(self, "device_camera_allowed", None)
        if is_ios:
            # The original capture supplies the model. A Windows/Android
            # profile cannot establish eligibility for an iOS recording.
            device = meta.get("device")
            model = device.get("model") if isinstance(device, dict) else None
            if not isinstance(model, str) or not model.strip():
                raise AuthError("Modelo do dispositivo ausente na captura iOS.", code="policy")
            allowed = self.camera_model_allowed(model, platform="ios")
        if allowed is not True:
            code = "policy" if allowed is False else "service"
            raise AuthError("Política de câmera não autorizada ou não verificada para esta sessão.", code=code)
        org_key = (parse_qs(urlsplit(path).query).get("org_key") or [""])[0]
        if not org_key:
            raise AuthError("Organização ausente no registro de upload.", code="policy")
        state = self.org_state(org_key)
        if state["blocked"]:
            account_disabled = state.get("account_disabled") is True
            code = "restricted" if account_disabled else "access_paused"
            raise AuthError("A organização ou a conta não permite novos envios.", code=code,
                            provider_error_code="user_account_disabled" if account_disabled else None)
        if state["userState"] != "active":
            raise AuthError("Estado de qualidade Minute não verificado.", code="invalid_response")
        self._check_recording_geo(org_key)
        sources = state.get("cameraSources")
        if not is_ios and (not isinstance(sources, list) or not sources):
            raise AuthError("Origens de gravação permitidas não foram confirmadas.", code="service")
        # meta.source descreve o formato (ego), não a origem da câmera.
        # A origem declarada de cada câmera está em meta.cameras[].source.
        cameras = meta.get("cameras") if isinstance(meta, dict) else None
        if not isinstance(cameras, list) or not cameras:
            raise AuthError("Origem da câmera ausente nos metadados do envio.", code="policy")
        camera_sources = []
        for camera in cameras:
            # The official client's effectiveCameraPolicy applies the
            # organization source restriction only on Android. Native iOS
            # metadata may omit source; do not infer or insert a value.
            if is_ios and isinstance(camera, dict) and "source" not in camera:
                continue
            source = camera.get("source") if isinstance(camera, dict) else None
            if source not in ("builtin", "built-in", "external"):
                raise AuthError("Origem da câmera inválida nos metadados do envio.", code="policy")
            camera_sources.append("built-in" if source == "builtin" else source)
        if not is_ios and any(source not in sources for source in camera_sources):
            raise AuthError("Origem da gravação não permitida pela organização.", code="policy")
        try:
            self.recording_policy.validate_duration(body.get("duration_ms") if isinstance(body, dict) else None)
            self.recording_policy.validate_recording_time(body.get("recorded_at"), body["duration_ms"], time.time())
        except ValueError as exc:
            raise AuthError(str(exc), code="policy") from exc

    def _check_recording_geo(self, org_key: str) -> None:
        """Respeita `recordingAuthorization` / `canUpload` do `/orgs/.../quota`."""
        quota = self.recording_geo(org_key)
        if not quota:
            cause = self._quota_failures.get(org_key)
            if cause is not None:
                raise cause
            raise AuthError(
                "Não foi possível verificar a autorização geográfica ou de quota.",
                code="service",
            )
        auth = quota.get("recordingAuthorization")
        can = quota.get("canUpload")
        reason = quota.get("blockedReason")
        if auth == "REQUIRES_LOCATION":
            raise AuthError(
                "O serviço exige localização do dispositivo para novos envios.",
                code="policy",
            )
        if auth == "BLOCKED" or can is False or reason in (
                "geo_restricted", "geo_unknown", "quota_exceeded", "manual"):
            label = reason or auth or "blocked"
            raise AuthError(
                f"Novos envios bloqueados pela autorização de gravação ({label}).",
                code="policy",
            )
        if auth == "APPROVED" and can is True and "blockedReason" in quota and reason is None:
            return
        raise AuthError(
            "Não foi possível confirmar autorização de gravação para esta organização.",
            code="service",
        )

    def warmup(self) -> None:
        """Atualiza políticas de leitura; `app/opened` só com flag explícita.

        Estado e cache pertencem à sessão. Respostas inválidas não são sucesso;
        uma próxima chamada tenta novamente com espera limitada. Políticas
        vencidas que não puderam ser renovadas impedem novos registros.
        Com `MINUTE_PUBLISH_APP_OPENED=1`, publica um `SESSION_RESUMED` após
        sucesso (uma vez por conta nesta sessão; falha tenta no próximo ciclo).
        """
        self._check_local_restrictions()
        with self._lock:
            now = time.monotonic()
            if self._recording_checked_at is not None and now - self._recording_checked_at < 60:
                return
            if now < self._recording_retry_at:
                return
            self.initialization_errors = {}
            self._initialization_causes = {}
            try:
                status, body = self.fetch_recording_config()
                if status != 200:
                    raise _auth_failure(status, body, "Consulta da configuração de gravação")
                payload = json.loads(body)
                self.recording_policy = RecordingPolicy.parse(payload)
                self.recording_config = payload
            except Exception as exc:
                self.initialization_errors["recording_config"] = "Não foi possível validar a configuração de gravação."
                self._initialization_causes["recording_config"] = (
                    exc if isinstance(exc, AuthError) else
                    AuthError("A configuração de gravação devolvida pelo serviço é inválida.",
                              code="invalid_response"))
            # Preserva a consulta existente, mas uma negativa deixa de ser aviso.
            self.device_camera_allowed = None
            try:
                model = device_profile.get_profile(self.email).device_model if self.email else ""
                if model:
                    self.device_camera_allowed = self.camera_model_allowed(model)
                if self.device_camera_allowed is None:
                    raise ValueError("Política não verificada.")
            except Exception:
                self.initialization_errors["camera_policy"] = "Não foi possível verificar a política de câmera."
            if self.initialization_errors:
                self._recording_checked_at = None
                failures = min(getattr(self, "_initialization_failures", 0) + 1, 5)
                self._initialization_failures = failures
                self._recording_retry_at = time.monotonic() + min(60, 5 * 2 ** (failures - 1))
            else:
                self._initialization_failures = 0
                self._recording_retry_at = 0.0
                self._recording_checked_at = time.monotonic()
                if config.PUBLISH_APP_OPENED and not getattr(self, "_app_opened_published", False):
                    try:
                        status, _ = self.app_opened("SESSION_RESUMED")
                        self._app_opened_published = 200 <= status < 300
                    except Exception:
                        pass
