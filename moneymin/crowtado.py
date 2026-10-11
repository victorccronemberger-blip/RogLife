"""
crowtado.py — Login no crowtado (Clerk FAPI) e consulta de saldo.

O crowtado usa Clerk (clerk.crowtado.com). O sign-in por senha NÃO exige
captcha (diferente do sign-up), então o login é 100% por API, sem navegador:

  1. POST /v1/client                    -> cria o client (cookie __client)
  2. POST /v1/client/sign_ins           -> identifier+password (strategy=password)
  3. POST /v1/client/sessions/{id}/tokens -> JWT de sessão (~60s de vida)
  4. GET /api/trpc/payouts.summary com Cookie __session=<jwt>

Como o JWT expira rápido, a sessão Clerk em memória emite um token novo sem
refazer o login. Somente o fallback de compatibilidade abre um navegador.

O sign-up exige captcha (Cloudflare Turnstile via Clerk), então `criar_conta`
usa um Chrome REAL lançado com `--remote-debugging-port` (perfil persistente em
`data/chrome-crowtado/`) controlado via CDP — navegador lançado pelo Playwright
não recebe token do Turnstile (navigator.webdriver=true). A verificação de
email é lida da caixa catch-all da Hostinger (moneymin.hostinger_mail).

Exemplo:
    from moneymin.crowtado import consultar_saldo_api
    print(consultar_saldo_api("user@example.com", "senha"))
"""
from __future__ import annotations

import hashlib
import http.cookiejar
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from . import config, tls

CLERK_BASE = "https://clerk.crowtado.com"
SITE_BASE = "https://www.crowtado.com"
EARNINGS_PATH = "/pt-BR/dashboard/earnings"
_CLERK_QS = "_clerk_js_version=5.88.0&__clerk_api_version=2024-10-01"
_ORIGIN = {"Origin": SITE_BASE, "Referer": SITE_BASE + "/"}

# Perfil persistente do Chrome real usado no sign-up (turnstile confia mais
# num perfil "vivido" — cookies/histórico acumulam entre execuções).
CHROME_PROFILE = config.DATA_DIR / "chrome-crowtado"
# Código de indicação padrão (aplicado na URL de sign-up). Fonte única:
# `config.CROWTADO_REF` — IMUTÁVEL, fixo no código.
DEFAULT_REF = config.CROWTADO_REF


class CrowtadoError(RuntimeError):
    """Falha de login ou consulta no crowtado."""

    def __init__(self, message: str, *, code: str | None = None, http_status: int | None = None,
                 retry_after_seconds: float | None = None, phase: str | None = None,
                 remote_effect_possible: bool | None = None,
                 provider_error_code: str | None = None):
        super().__init__(message)
        self.account_issue_code = code
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds
        self.phase = phase
        self.remote_effect_possible = remote_effect_possible
        self.provider_error_code = provider_error_code


def _retry_after_seconds(headers) -> float | None:
    """Read the provider's cooldown without retaining response headers."""
    value = None
    if headers is not None:
        try:
            value = headers.get("Retry-After")
            if value is None:
                value = next((item for key, item in headers.items()
                              if str(key).casefold() == "retry-after"), None)
        except (AttributeError, TypeError):
            return None
    if not value:
        return None
    try:
        seconds = float(value) if re.fullmatch(r"\d+", str(value).strip()) else (
            parsedate_to_datetime(str(value)).timestamp() - time.time())
        return max(1.0, seconds) if 0 <= seconds < float("inf") else None
    except (ValueError, TypeError, OverflowError):
        return None


def _finite_nonnegative_seconds(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        seconds = float(value)
    except (OverflowError, ValueError):
        return None
    return seconds if 0 <= seconds < float("inf") else None


def _provider_retry_after(body: Any) -> float | None:
    errors = body.get("errors", []) if isinstance(body, dict) else []
    if not isinstance(errors, list):
        return None
    for item in errors:
        if not isinstance(item, dict):
            continue
        meta = item.get("meta")
        if not isinstance(meta, dict):
            continue
        seconds = _finite_nonnegative_seconds(meta.get("lockout_expires_in_seconds"))
        if seconds is not None:
            return seconds
    return None


_SAFE_PROVIDER_ERROR_CODES = frozenset({
    "user_banned", "user_locked", "user_account_disabled", "user_disabled",
    "form_identifier_not_found", "form_password_incorrect", "form_identifier_exists",
})


def _safe_provider_error_code(body: Any) -> str | None:
    errors = body.get("errors", []) if isinstance(body, dict) else []
    if not isinstance(errors, list):
        return None
    for item in errors:
        code = item.get("code") if isinstance(item, dict) else None
        if isinstance(code, str) and code in _SAFE_PROVIDER_ERROR_CODES:
            return code
    return None


def _remote_error(stage: str, status: int, body: Any) -> CrowtadoError:
    errors = body.get("errors", []) if isinstance(body, dict) else []
    errors = errors if isinstance(errors, list) else []
    codes = {str(item.get("code")) for item in errors if isinstance(item, dict)}
    banned = codes & {"user_banned", "user_account_disabled", "user_disabled"}
    locked = "user_locked" in codes
    code = ("restricted" if banned else "account_locked" if locked else
            "crowtado_account_missing" if "form_identifier_not_found" in codes else
            "authentication" if codes & {"form_password_incorrect", "form_password_pwned", "session_invalid"}
            or status == 401 else
            "rate_limit" if status == 429 else
            "service" if status >= 500 else
            "forbidden" if status == 403 else "invalid_response")
    message = ("A Crowtado confirmou que esta conta está banida ou desativada. Fale com o suporte"
               if banned else "A Crowtado bloqueou o acesso desta conta"
               if locked else stage)
    retry_after = _provider_retry_after(body) if locked else None
    if locked and retry_after is not None:
        message += f". Tente novamente em {int(retry_after + 0.999)} segundos"
    return CrowtadoError(f"{message} (HTTP {status})", code=code, http_status=status,
                         retry_after_seconds=retry_after,
                         provider_error_code=_safe_provider_error_code(body))


def _is_clerk_signup_url(url: str) -> bool:
    """Only the configured Clerk origin can provide signup diagnostics."""
    try:
        parsed = urllib.parse.urlsplit(url)
        expected = urllib.parse.urlsplit(CLERK_BASE)
        return (parsed.scheme == "https" and parsed.netloc.casefold() == expected.netloc.casefold()
                and parsed.username is None and parsed.password is None
                and (parsed.path == "/v1/client/sign_ups"
                     or parsed.path.startswith("/v1/client/sign_ups/")))
    except (TypeError, ValueError):
        return False


def _signup_response_error(url: str, status: int, payload: Any, headers: Any,
                           phase: str) -> CrowtadoError | None:
    """Reduce a Clerk signup response to safe status metadata only."""
    try:
        if not _is_clerk_signup_url(url) or status < 400:
            return None
        error = _remote_error("O cadastro Crowtado foi recusado", status, payload)
        errors = payload.get("errors", []) if isinstance(payload, dict) else []
        codes = {str(row.get("code")) for row in errors if isinstance(row, dict)} if isinstance(errors, list) else set()
        if "form_identifier_exists" in codes:
            error = CrowtadoError("Esta conta já existe no Crowtado.", code="account_exists", http_status=status,
                                  provider_error_code="form_identifier_exists")
        elif status == 400 and error.account_issue_code == "invalid_response" and "captcha_invalid" in codes:
            error = CrowtadoError(
                "O provedor recusou a verificação CAPTCHA. O lote foi interrompido; o cadastro não foi confirmado e o envio não será repetido automaticamente.",
                code="captcha_required", http_status=status)
        if error.account_issue_code == "rate_limit":
            error.retry_after_seconds = _retry_after_seconds(headers)
        error.phase = phase
        error.remote_effect_possible = True
        return error
    except Exception:
        # Do not retain provider payloads if a response is malformed.
        return None


def _signup_response_error_from_response(response: Any, phase: str) -> CrowtadoError | None:
    """Extract only the signup response metadata needed for safe classification."""
    try:
        url = response.url
        status = int(response.status)
        if not _is_clerk_signup_url(url) or status < 400:
            return None
        headers = response.headers
    except Exception:
        return None
    try:
        payload = response.json()
    except Exception:
        payload = None
    return _signup_response_error(url, status, payload, headers, phase)


def _signup_session_response_error(response: Any, phase: str) -> CrowtadoError | None:
    """Preserve an explicit account restriction reported while activating signup."""
    if phase not in {"email_verification", "signup_confirmation"}:
        return None
    try:
        url = urllib.parse.urlsplit(response.url)
        expected = urllib.parse.urlsplit(CLERK_BASE)
        if (url.scheme != "https" or url.netloc.casefold() != expected.netloc.casefold()
                or url.username is not None or url.password is not None
                or not (url.path == "/v1/client" or url.path.startswith("/v1/client/sessions/"))
                or response.status < 400):
            return None
        error = _remote_error("A sessão do cadastro não foi confirmada", response.status, response.json())
        if error.account_issue_code not in {"restricted", "account_locked", "rate_limit"}:
            return None
        if error.account_issue_code == "rate_limit":
            error.retry_after_seconds = _retry_after_seconds(response.headers)
        error.phase = phase
        error.remote_effect_possible = True
        return error
    except Exception:
        return None


def _same_site_origin(url: str) -> bool:
    """Accept only HTTPS URLs on the configured public Crowtado origin."""
    try:
        parsed = urllib.parse.urlsplit(url)
        expected = urllib.parse.urlsplit(SITE_BASE)
        return (parsed.scheme == "https" and parsed.netloc.casefold() == expected.netloc.casefold()
                and parsed.username is None and parsed.password is None)
    except (TypeError, ValueError):
        return False


def _is_signup_confirmation_url(url: str) -> bool:
    """A Clerk redirect is confirmed only on an official post-signup route."""
    if not isinstance(url, str) or not _same_site_origin(url):
        return False
    try:
        path = urllib.parse.urlsplit(url).path.rstrip("/")
    except (TypeError, ValueError):
        return False
    # The captured contract uses locale-prefixed paths. No-locale paths are
    # intentionally excluded until the provider contract documents them.
    return bool(re.fullmatch(r"/[A-Za-z]{2}(?:-[A-Za-z]{2})?/(?:complete-profile|dashboard)", path))


def _is_signup_session_transfer_url(url: str) -> bool:
    """An official session handoff may redirect back without a second login."""
    if not isinstance(url, str):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        redirects = urllib.parse.parse_qs(parsed.query, max_num_fields=20).get("redirect_url", [])
        return (parsed.scheme == "https" and parsed.netloc.casefold() == "accounts.crowtado.com"
                and parsed.path.rstrip("/") == "/sign-in" and len(redirects) == 1
                and _is_signup_confirmation_url(redirects[0]))
    except (TypeError, ValueError):
        return False


def _is_signup_submission_url(url: str) -> bool:
    """Recognize the official localized signup route, excluding sign-in and query tricks."""
    if not isinstance(url, str) or not _same_site_origin(url):
        return False
    try:
        path = urllib.parse.urlsplit(url).path.rstrip("/")
    except (TypeError, ValueError):
        return False
    return bool(re.fullmatch(r"/[A-Za-z]{2}(?:-[A-Za-z]{2})?/sign-up(?:/.*)?", path))


def _is_signup_verification_url(url: str) -> bool:
    """Only recognize a verification step under the official signup route."""
    if not isinstance(url, str) or not _same_site_origin(url):
        return False
    try:
        path = urllib.parse.urlsplit(url).path.rstrip("/")
    except (TypeError, ValueError):
        return False
    return bool(re.fullmatch(
        r"/[A-Za-z]{2}(?:-[A-Za-z]{2})?/sign-up/verify(?:[-/][A-Za-z0-9_-]+)*", path))


def _signup_proof_response_error(url: str, status: int, headers: Any, *,
                                 phase: str = "signup_preparation",
                                 remote_effect_possible: bool = False) -> CrowtadoError | None:
    """Classify only an HTTP failure from the official proof endpoint.

    The response body, sealed result, proof ID, and URL query are never read or
    retained. This is a diagnostic of the frontend's normal proof request;
    it does not create, replay, or alter proof data.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
        if (not _same_site_origin(url) or parsed.path != "/api/fraud/signup-fingerprint-proof"
                or type(status) is not int or status < 400):
            return None
    except (TypeError, ValueError):
        return None
    limited = status == 429
    return CrowtadoError(
        "A preparação oficial do cadastro Crowtado foi recusada; confira o site ou contate o suporte.",
        code="rate_limit" if limited else "device", http_status=status,
        retry_after_seconds=_retry_after_seconds(headers) if limited else None,
        phase=phase, remote_effect_possible=remote_effect_possible)


def _signup_proof_response_error_from_response(response: Any, *, phase: str = "signup_preparation",
                                              remote_effect_possible: bool = False) -> CrowtadoError | None:
    """Inspect only URL, status, and Retry-After on the official proof failure."""
    try:
        return _signup_proof_response_error(response.url, int(response.status), response.headers,
                                            phase=phase,
                                            remote_effect_possible=remote_effect_possible)
    except (AttributeError, TypeError, ValueError):
        return None


def can_use_browser_fallback(error: Exception) -> bool:
    # Repeating a rejected password, rate limit or outage in Chrome only
    # duplicates requests and hides the original diagnosis.
    return getattr(error, "account_issue_code", None) not in {
        "authentication", "crowtado_account_missing", "rate_limit", "service", "account_locked",
        "network", "timeout", "tls", "email_verification", "mail_authentication", "restricted", "device",
    }


class CrowtadoSession:
    """Sessão autenticada: cookie jar do Clerk + JWT de sessão renovável."""

    def __init__(self, opener: urllib.request.OpenerDirector, session_id: str):
        self.opener = opener
        self.session_id = session_id

    def _fapi(self, path: str, data: dict[str, str] | None = None) -> tuple[int, Any]:
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(
            f"{CLERK_BASE}{path}?{_CLERK_QS}", data=body, method="POST" if data is not None else "GET"
        )
        for k, v in _ORIGIN.items():
            req.add_header(k, v)
        # Cloudflare (erro 1010) bloqueia o UA padrão do urllib — usar UA neutro.
        req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/151.0")
        if body is not None:
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with self.opener.open(req, timeout=30) as resp:
                try:
                    return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
                except (ValueError, UnicodeError) as exc:
                    raise CrowtadoError("Resposta inválida da autenticação Crowtado",
                                        code="invalid_response") from exc
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace")
            try:
                payload = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                payload = text
            if exc.code == 429:
                error = _remote_error("Consulta de autenticação Crowtado limitada", exc.code, payload)
                error.retry_after_seconds = _retry_after_seconds(exc.headers)
                raise error from exc
            return exc.code, payload
        except urllib.error.URLError as exc:
            detail = str(getattr(exc, "reason", exc))
            if "CERTIFICATE_VERIFY_FAILED" in detail.upper():
                raise CrowtadoError(
                    "a conexão segura não pôde ser validada; use Reparar "
                    "instalação na aba Integrações e tente novamente", code="tls"
                ) from exc
            raise CrowtadoError(
                "não foi possível conectar ao Crowtado; confira a internet e tente novamente", code="network"
            ) from exc
        except TimeoutError as exc:
            raise CrowtadoError("Tempo esgotado na autenticação Crowtado", code="timeout") from exc

    def session_jwt(self) -> str:
        """Emite um JWT novo apenas para a sessão autenticada desta conta."""
        if not self.session_id:
            raise CrowtadoError("Sessão Crowtado ausente", code="authentication")
        status, body = self._fapi(f"/v1/client/sessions/{self.session_id}/tokens", {})
        if status != 200:
            if status == 404:
                raise CrowtadoError("Sessão Crowtado expirada", code="authentication", http_status=status)
            raise _remote_error("Renovação da sessão Crowtado recusada", status, body)
        payload = body.get("response", body) if isinstance(body, dict) else {}
        jwt = payload.get("jwt") if isinstance(payload, dict) else None
        if not isinstance(jwt, str) or not jwt.strip():
            raise CrowtadoError("Sessão Crowtado sem token válido", code="invalid_response")
        return jwt


_SESSION_LOCK = threading.Lock()
_SESSION_CACHE: dict[str, tuple[str, str | None, CrowtadoSession]] = {}
_LOGIN_LOCKS = [threading.RLock() for _ in range(64)]


def _login_lock(email: str):
    # Bounded lock storage; the same account cannot request competing OTPs.
    key = email.strip().casefold().encode("utf-8")
    return _LOGIN_LOCKS[int.from_bytes(hashlib.sha256(key).digest()[:2], "big") % 64]


def _password_fingerprint(password: str) -> str:
    """Identifica troca de senha sem guardar outra cópia dela na memória."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def clear_cached_session(email: str | None = None) -> None:
    """Descarta uma sessão específica ou todo o cache autenticado em memória."""
    with _SESSION_LOCK:
        if email is None:
            _SESSION_CACHE.clear()
        else:
            _SESSION_CACHE.pop(email.strip().casefold(), None)


def _cached_login(email: str, password: str) -> CrowtadoSession:
    with _login_lock(email):
        return _cached_login_locked(email, password)


def _cached_login_locked(email: str, password: str) -> CrowtadoSession:
    """Reaproveita o client Clerk enquanto ele ainda consegue emitir um JWT."""
    from .registration_proxy import endpoint

    email = email.strip().casefold()
    fingerprint = _password_fingerprint(password)
    current_proxy = endpoint()
    with _SESSION_LOCK:
        cached = _SESSION_CACHE.get(email)
    if cached:
        # Legacy two-item entries came from direct-route sessions. Keep them
        # reusable for direct reads, but never carry them into an assigned route.
        cached_fingerprint, cached_proxy, cached_session = (
            (cached[0], cached[1], cached[2]) if len(cached) == 3
            else (cached[0], None, cached[1]))
    else:
        cached_fingerprint = cached_proxy = cached_session = None
    if cached and cached_fingerprint == fingerprint and cached_proxy == current_proxy:
        try:
            cached_session.session_jwt()
            return cached_session
        except CrowtadoError as exc:
            if exc.account_issue_code != "authentication":
                raise
            clear_cached_session(email)
    session = login(email, password)
    with _SESSION_LOCK:
        _SESSION_CACHE[email] = (fingerprint, current_proxy, session)
    return session


def login(email: str, password: str) -> CrowtadoSession:
    with _login_lock(email):
        return _login_locked(email, password)


def _check_login_user_flags(payload: Any, session_id: str) -> None:
    """Use explicit flags for the authenticated user, never infer bans from 403."""
    if not isinstance(payload, dict):
        return
    users = []
    response = payload.get("response")
    if isinstance(response, dict) and isinstance(response.get("user"), dict):
        users.append(response["user"])
    client = payload.get("client")
    if isinstance(client, dict):
        sessions = client.get("sessions") or []
        if isinstance(sessions, list):
            users.extend(row["user"] for row in sessions if isinstance(row, dict)
                         and row.get("id") == session_id and isinstance(row.get("user"), dict))
    for user in users:
        for flag in ("banned", "locked"):
            if flag in user and type(user[flag]) is not bool:
                raise CrowtadoError("A Crowtado retornou um estado de conta inválido; verifique novamente.", code="invalid_response")
        if user.get("banned") is True:
            raise CrowtadoError("A Crowtado confirmou que esta conta está banida. Fale com o suporte.",
                                code="restricted", provider_error_code="user_banned")
        if user.get("locked") is True:
            retry_after = _finite_nonnegative_seconds(user.get("lockout_expires_in_seconds"))
            message = "A Crowtado bloqueou o acesso desta conta"
            if retry_after is not None:
                message += f". Tente novamente em {int(retry_after + 0.999)} segundos"
            raise CrowtadoError(message + ".", code="account_locked", retry_after_seconds=retry_after)


def _login_locked(email: str, password: str) -> CrowtadoSession:
    """Autentica no Clerk por senha. Levanta CrowtadoError se falhar.

    Se a conta pedir segundo fator por email (email_code), busca o código na
    caixa catch-all da Hostinger (moneymin.hostinger_mail) e confirma.
    """
    jar = http.cookiejar.CookieJar()
    opener = tls.build_opener(urllib.request.HTTPCookieProcessor(jar))
    sess = CrowtadoSession(opener, "")

    status, body = sess._fapi("/v1/client", {})
    if status != 200:
        raise _remote_error("Criação da sessão Crowtado falhou", status, body)

    status, body = sess._fapi(
        "/v1/client/sign_ins",
        {"identifier": email, "password": password, "strategy": "password"},
    )
    sign_in = body.get("response") if isinstance(body, dict) else None
    sign_in = sign_in if isinstance(sign_in, dict) else {}
    si_status = sign_in.get("status")

    if si_status == "needs_second_factor":
        sid_sign_in = sign_in.get("id")
        fatores = {f.get("strategy") for f in sign_in.get("supported_second_factors") or []}
        if "email_code" not in fatores:
            raise CrowtadoError(f"2º fator não suportado por este cliente: {fatores}")
        # Snapshot ANTES de pedir o código: ignora emails antigos na caixa.
        from .hostinger_mail import max_uid, wait_for_code, MailAuthenticationError

        try:
            uid_base = max_uid(email)
        except Exception as exc:
            raise CrowtadoError("Não foi possível consultar o código por e-mail da Crowtado",
                                code="mail_authentication" if isinstance(exc, MailAuthenticationError)
                                else "email_verification") from exc
        status, body = sess._fapi(
            f"/v1/client/sign_ins/{sid_sign_in}/prepare_second_factor",
            {"strategy": "email_code"},
        )
        if status != 200:
            raise _remote_error("Verificação por e-mail Crowtado falhou", status, body)
        # Código chega no email da conta (caixa catch-all da Hostinger).
        try:
            code = wait_for_code(email, sender="crowtado.com", min_uid=uid_base, timeout=180)
        except Exception as exc:
            raise CrowtadoError("Não foi possível obter o código por e-mail da Crowtado",
                                code="mail_authentication" if isinstance(exc, MailAuthenticationError)
                                else "email_verification") from exc
        status, body = sess._fapi(
            f"/v1/client/sign_ins/{sid_sign_in}/attempt_second_factor",
            {"strategy": "email_code", "code": code},
        )
        sign_in = body.get("response") if isinstance(body, dict) else None
        sign_in = sign_in if isinstance(sign_in, dict) else {}
        si_status = sign_in.get("status")

    sid = sign_in.get("created_session_id")
    if status != 200 or si_status != "complete" or not sid:
        raise _remote_error("Login Crowtado não concluído", status, body)
    _check_login_user_flags(body, sid)
    sess.session_id = sid
    return sess


def _extrai_summary(texto: str) -> dict[str, int]:
    """Extrai o payouts.summary de um payload flight RSC (com escapes ou não)."""
    decoder = json.JSONDecoder()
    # Decode the enclosing object rather than stopping at a nested destination.
    # Limit scanning to avoid quadratic work on an unrelated Flight document.
    for normalized in (texto, re.sub(r'\\+"', '"', texto)):
        for match in list(re.finditer(r'"availableCents"\s*:', normalized))[:32]:
            starts = [m.start() for m in re.finditer(r'\{', normalized[max(0, match.start()-8192):match.start()])]
            offset = max(0, match.start()-8192)
            for start in reversed(starts[-32:]):
                try:
                    payload, _ = decoder.raw_decode(normalized, offset + start)
                    result = _summary_from_payload(payload)
                    if result:
                        return result
                except (ValueError, CrowtadoError):
                    continue
    return {}


def _read_balance_summary(session: CrowtadoSession) -> dict[str, Any]:
    """Retry one transient GET using the same authenticated session."""
    for attempt in range(2):
        try:
            payload = _site_trpc(session, "payouts.summary", None, method="GET")
            summary = _summary_from_payload(payload)
            if not summary:
                raise CrowtadoError("payouts.summary sem saldo completo reconhecível", code="invalid_response")
            return summary
        except CrowtadoError as exc:
            if attempt or exc.account_issue_code not in {"network", "timeout", "service", "invalid_response"}:
                raise
            time.sleep(0.5)
    raise AssertionError("unreachable")


BALANCE_SUMMARY_FIELDS = frozenset({
    "availableCents", "inTransitCents", "lifetimeCents", "pendingCents",
    "onHoldCents", "notApprovedCents", "pendingCount", "notApprovedCount", "withdrawMinimumCents",
    "inTransitReplaceable", "pendingIsEstimate", "dotsReady", "wiseReady", "bankTransferViaTremendous",
    "currency", "payoutPreference", "onHoldReason", "holdReason", "holdReleaseAt",
    "contributorEligibility",
})


def _summary_from_payload(payload: Any) -> dict[str, Any]:
    """Normaliza as formas direta/aninhada usadas pelo payouts.summary."""
    required = ("availableCents", "inTransitCents", "lifetimeCents", "pendingCents")
    if isinstance(payload, dict):
        if all(name in payload for name in required):
            try:
                values = {}
                for name in (*required, "onHoldCents", "notApprovedCents", "pendingCount", "notApprovedCount", "withdrawMinimumCents"):
                    if name not in payload and name not in required:
                        continue
                    value = payload[name]
                    if isinstance(value, bool) or not (
                            isinstance(value, int) or isinstance(value, str)
                            and re.fullmatch(r"-?\d+", value)):
                        raise ValueError("invalid cents")
                    values[name] = int(value)
                    if not 0 <= values[name] <= 2**53 - 1:
                        raise ValueError("invalid cents range")
                for name in ("inTransitReplaceable", "pendingIsEstimate", "dotsReady", "wiseReady", "bankTransferViaTremendous"):
                    if name in payload:
                        if type(payload[name]) is not bool:
                            raise ValueError("invalid flag")
                        values[name] = payload[name]
                for name in ("currency", "payoutPreference", "onHoldReason", "holdReason", "holdReleaseAt"):
                    if name in payload:
                        value = payload[name]
                        if value is not None and (not isinstance(value, str) or len(value) > 500):
                            raise ValueError("invalid payout state")
                        values[name] = value
                if "contributorEligibility" in payload:
                    values["contributorEligibility"] = _normalize_eligibility(payload["contributorEligibility"])
                return values
            except (TypeError, ValueError, OverflowError) as exc:
                raise CrowtadoError("payouts.summary devolveu valores inválidos", code="invalid_response") from exc
        for name in ("summary", "payouts", "data", "result", "json"):
            if name in payload:
                summary = _summary_from_payload(payload[name])
                if summary:
                    return summary
    elif isinstance(payload, list):
        for item in payload:
            summary = _summary_from_payload(item)
            if summary:
                return summary
    return {}


def _normalize_eligibility(payload: Any) -> dict[str, Any]:
    """Only retain public eligibility flags, never device or identity details."""
    if isinstance(payload, dict):
        if type(payload.get("available")) is bool and type(payload.get("blocked")) is bool:
            return {"checked": payload.get("checked", True) is True,
                    "available": payload["available"], "blocked": payload["blocked"],
                    "withdrawalOverride": payload.get("withdrawalOverride") is True,
                    "reasons": sorted({reason for reason in payload.get("reasons", [])
                        if isinstance(reason, str) and reason in {"vpn", "device", "account"}})
                        if isinstance(payload.get("reasons", []), list) else []}
        for key in ("result", "data", "json"):
            if key in payload:
                result = _normalize_eligibility(payload[key])
                if result["checked"]:
                    return result
    if isinstance(payload, list):
        for item in payload:
            result = _normalize_eligibility(item)
            if result["checked"]:
                return result
    return {"checked": False}


def _read_contributor_eligibility(session: CrowtadoSession) -> dict[str, Any]:
    try:
        payload = _site_trpc(session, "externalMobileCapture.eligibilityStatus", None, method="GET")
        return _normalize_eligibility(payload)
    except Exception:
        # Eligibility failure must not erase a successfully read monetary balance.
        return {"checked": False}


def consultar_saldo_api(email: str, senha: str) -> dict[str, Any]:
    """Consulta o saldo pela API tRPC, sem iniciar navegador."""
    session = _cached_login(email, senha)
    try:
        summary = _read_balance_summary(session)
    except CrowtadoError as exc:
        if exc.account_issue_code != "authentication":
            raise
        # Only the read is retried. Never replay a financial mutation.
        clear_cached_session(email)
        session = _cached_login(email, senha)
        summary = _read_balance_summary(session)
    summary["contributorEligibility"] = _read_contributor_eligibility(session)
    return summary


def verificar_restricoes(email: str, senha: str) -> dict[str, Any]:
    """Fresh authentication and read-only payout checks; never request a withdrawal."""
    session = login(email, senha)
    try:
        summary = _read_balance_summary(session)
        if summary.get("onHoldReason") or summary.get("holdReason"):
            return _restricoes_from_summary(summary, {})
        eligibility = _site_trpc(session, "externalMobileCapture.eligibilityStatus", None, method="GET")
        return _restricoes_from_summary(summary, eligibility)
    except Exception as exc:
        exc.crowtado_login_verified = True
        raise


def _restricoes_from_summary(summary: dict[str, Any], payload: Any) -> dict[str, Any]:
    """Keep explicit payout evidence even when another read is unavailable."""
    if summary.get("onHoldReason") or summary.get("holdReason"):
        return {"restricted": True, "restriction_kind": "payout",
                "reason": "A Crowtado informa retenção de saque. Confira o motivo no painel Crowtado."}
    eligibility = _normalize_eligibility(payload)
    if eligibility.get("checked") is not True:
        raise CrowtadoError("Elegibilidade Crowtado não confirmada", code="invalid_response")
    if eligibility.get("withdrawalOverride") is not True and eligibility.get("blocked") is True:
        reasons = eligibility.get("reasons", [])
        reason = ("A Crowtado informa conta desativada e retém os saques. Solicite regularização ao suporte."
                  if "account" in reasons else
                  "A Crowtado bloqueou a elegibilidade por VPN." if "vpn" in reasons else
                  "A Crowtado bloqueou a elegibilidade do dispositivo." if "device" in reasons else
                  "A Crowtado informou bloqueio de elegibilidade de saque.")
        return {"restricted": True, "restriction_kind": "payout", "reason": reason}
    return {"restricted": False, "restriction_kind": "none", "payout_available":
            eligibility.get("withdrawalOverride") is True or eligibility.get("available") is True}


_WITHDRAW_RESULT_FIELDS = {
    "status",
    "amountCents",
    "currency",
    "thresholdCents",
    "minimumCents",
    "holdReason",
    "failureReason",
    "dotsEmailDelivery",
    "dotsSmsDelivery",
    "rail",
}


def configurar_metodo_saque(email: str, senha: str, method: str,
                            legal_name: str = "", destination_email: str = "") -> None:
    """Salva o destino e a preferência de saque de uma conta Crowtado."""
    if method not in {"dots", "paypal", "wise"}:
        raise CrowtadoError("método de saque inválido")
    session = _cached_login(email, senha)
    available = _site_trpc(session, "payouts.payoutMethods", None, method="GET")
    if not isinstance(available, list):
        raise CrowtadoError("não foi possível verificar os métodos disponíveis")
    remote_method = ("other" if "other" in available else "dots") if method == "dots" else method
    if remote_method not in available:
        raise CrowtadoError(f"{method} não está disponível para esta conta")
    if method == "paypal" and (legal_name or destination_email):
        raise CrowtadoError("PayPal usa o fluxo de pagamento da Crowtado; não informe um destino manual.",
                            code="payout_configuration")
    if method == "wise":
        legal_name = legal_name.strip()
        destination_email = destination_email.strip()
        if len(legal_name) < 2 or not destination_email or "@" not in destination_email or len(destination_email) > 254:
            raise CrowtadoError("informe nome legal e e-mail válido do destino")
        manual_payload = {
            "method": method, "legalName": legal_name,
            "paypalReceiverType": "email", "payoutCurrency": "USD",
            "makePreferred": True,
        }
        manual_payload["wiseEmail"] = destination_email
        _site_trpc(session, "kyc.saveManualPayoutMethod", manual_payload)
    _site_trpc(session, "payouts.savePayoutPreference", {"method": remote_method})
    summary = _wait_payout_state(session, lambda row: (
        row.get("payoutPreference") == remote_method and
        (method != "wise" or (row.get("wiseReady") is True and any(
            isinstance(item, dict) and item.get("method") == "wise" and item.get("isPreferred") is True
            for item in row.get("manualDestinations") or [])))))
    if not isinstance(summary, dict) or summary.get("payoutPreference") != remote_method:
        raise CrowtadoError("o Crowtado não confirmou a preferência salva")
    if method == "wise" and not summary.get("wiseReady"):
        raise CrowtadoError("Wise foi salvo, mas ainda não está disponível para saque")
    if method == "wise":
        destinations = summary.get("manualDestinations") or []
        if not any(isinstance(item, dict) and item.get("method") == method
                   and item.get("isPreferred") for item in destinations):
            raise CrowtadoError("o Crowtado não confirmou o destino preferido")


def _wait_payout_state(session: CrowtadoSession, predicate) -> dict[str, Any]:
    """Only repeat reads while the platform propagates a configuration change."""
    for attempt in range(4):
        if attempt:
            time.sleep(1)
        summary = _site_trpc(session, "payouts.summary", None, method="GET")
        if isinstance(summary, dict) and predicate(summary):
            return summary
    raise CrowtadoError("A Crowtado ainda não confirmou a alteração do método de saque.",
                        code="payout_configuration")


def desvincular_wise(email: str, senha: str) -> None:
    """Remove o destino Wise e confirma que ele deixou de estar vinculado."""
    session = _cached_login(email, senha)
    _site_trpc(session, "kyc.removeManualPayoutMethod", {"method": "wise"})
    summary = _wait_payout_state(session, lambda row: (
        row.get("wiseReady") is False and isinstance(row.get("manualDestinations"), list)
        and all(isinstance(item, dict) and item.get("method") != "wise"
                for item in row["manualDestinations"])))
    if (not isinstance(summary, dict) or summary.get("wiseReady") is not False
            or not isinstance(summary.get("manualDestinations"), list)
            or any(item.get("method") == "wise"
                   for item in summary.get("manualDestinations") or []
                   if isinstance(item, dict))):
        raise CrowtadoError("a desvinculação da Wise não foi confirmada")


def finalizar_wise(email: str, senha: str) -> dict[str, bool]:
    """Limpeza idempotente: nunca solicita saque e confirma o estado final remoto."""
    try:
        session = _cached_login(email, senha)
        summary = _site_trpc(session, "payouts.summary", None, method="GET")
        if not isinstance(summary, dict) or not isinstance(summary.get("manualDestinations"), list):
            raise CrowtadoError("não foi possível confirmar os destinos")
        if summary.get("wiseReady") or any(
                isinstance(item, dict) and item.get("method") == "wise"
                for item in summary["manualDestinations"]):
            desvincular_wise(email, senha)
    except Exception:
        pass  # Ainda tentamos restaurar Dots; o estado final decide o resultado.
    try:
        configurar_metodo_saque(email, senha, "dots")
    except Exception:
        pass
    result = {"wiseDestinationRemoved": False, "payoutPreferenceRestored": False}
    try:
        session = _cached_login(email, senha)
        summary = _site_trpc(session, "payouts.summary", None, method="GET")
        if isinstance(summary, dict):
            destinations = summary.get("manualDestinations")
            result["wiseDestinationRemoved"] = (
                summary.get("wiseReady") is False and isinstance(destinations, list)
                and all(isinstance(item, dict) and item.get("method") != "wise"
                        for item in destinations))
            result["payoutPreferenceRestored"] = summary.get("payoutPreference") in {"other", "dots"}
    except Exception:
        pass
    return result


def payout_in_transit(summary: dict[str, Any]) -> bool:
    cents = summary.get("inTransitCents")
    return (isinstance(cents, (int, float)) and not isinstance(cents, bool)
            and cents > 0 and summary.get("inTransitReplaceable") is not True)


def solicitar_link_saque(email: str, senha: str, *, expected_method: str | None = None,
                         cleanup_wise: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        result = _request_withdrawal(email, senha, expected_method=expected_method)
    except Exception as exc:
        if not hasattr(exc, "withdrawal_attempted"):
            exc.withdrawal_attempted = False
        raise
    finally:
        if cleanup_wise and expected_method == "wise":
            cleanup = finalizar_wise(email, senha)
            result.update(cleanup)
            result["cleanupPending"] = not all(cleanup.values())
    return result


def _request_withdrawal(email: str, senha: str, *, expected_method: str | None = None) -> dict[str, Any]:
    """Solicita saque com o método preferido salvo no Crowtado."""
    session = _cached_login(email, senha)
    summary = _site_trpc(session, "payouts.summary", None, method="GET")
    if not isinstance(summary, dict):
        raise CrowtadoError("não foi possível consultar o método de saque")
    if payout_in_transit(summary):
        return {"status": "in_transit"}
    method = summary.get("payoutPreference")
    if expected_method and method != expected_method:
        raise CrowtadoError("o método confirmado não corresponde ao solicitado; saque não enviado")
    if method == "wise" and expected_method != "wise":
        raise CrowtadoError("selecione e confirme Wise ao solicitar saque")
    if not method:
        raise CrowtadoError("método de saque não confirmado; configure-o antes de sacar")
    if method not in {"dots", "other", "bank_transfer", "paypal", "wise"}:
        raise CrowtadoError("método de saque configurado não é suportado pelo QMoney")
    if method == "wise" and not summary.get("wiseReady"):
        raise CrowtadoError("Wise não está disponível para saque nesta conta")
    if method == "wise":
        destinations = summary.get("manualDestinations") or []
        if not any(isinstance(item, dict) and item.get("method") == method
                   and item.get("isPreferred") for item in destinations):
            raise CrowtadoError("destino preferido não confirmado; saque não solicitado")
    confirmed = _summary_from_payload(summary)
    if not confirmed:
        raise CrowtadoError("saldo atual incompleto; saque não enviado", code="invalid_response")
    if confirmed.get("currency", "USD") != "USD":
        raise CrowtadoError("moeda do saldo atual não confirmada; saque não enviado")
    if confirmed.get("onHoldReason") or confirmed.get("holdReason"):
        return {"status": "on_hold", "holdReason": confirmed.get("onHoldReason") or confirmed["holdReason"],
                "withdrawalAttempted": False}
    if confirmed["availableCents"] <= 2500:
        return {"status": "below_minimum", "thresholdCents": 2500}
    eligibility = _read_contributor_eligibility(session)
    if not eligibility.get("checked") or (not eligibility.get("withdrawalOverride") and eligibility.get("available") is not True):
        return {"status": "eligibility_unconfirmed", "withdrawalAttempted": False}
    if not eligibility.get("withdrawalOverride") and eligibility.get("blocked") is not False:
        reasons = eligibility.get("reasons", [])
        reason = ("Conta desativada — fale com o suporte." if "account" in reasons else
                  "VPN detectada — confira a elegibilidade no Minute." if "vpn" in reasons else
                  "Dispositivo não compatível — confira a elegibilidade no Minute." if "device" in reasons else
                  "Elegibilidade de saque bloqueada no Minute — confira o painel da Crowtado.")
        return {"status": "on_hold", "holdReason": reason, "withdrawalAttempted": False}
    try:
        payload = _site_trpc(
            session, "payouts.withdraw", {"method": method}, method="POST")
    except Exception as exc:
        exc.withdrawal_attempted = True
        raise
    if not isinstance(payload, dict) or not payload.get("status"):
        error = CrowtadoError("A Crowtado devolveu uma resposta de saque inválida.", code="invalid_response")
        error.withdrawal_attempted = True
        raise error
    # Não repassa links/tokens ou campos novos desconhecidos para a interface.
    result = {key: value for key, value in payload.items()
              if key in _WITHDRAW_RESULT_FIELDS}
    return result


def consultar_saldo_navegador(
        email: str, senha: str, headed: bool = True) -> dict[str, int]:
    """Login no crowtado pelo navegador e leitura do saldo (available/pending).

    O sign-in NÃO tem captcha (diferente do sign-up): preenche email+senha no
    formulário do Clerk e, se pedir 2º fator, busca o código na caixa catch-all
    da Hostinger e digita. Depois abre /dashboard/earnings e captura a resposta
    do tRPC payouts.summary (availableCents, pendingCents, ...).

    headed=True (default) porque o Cloudflare barra headless com mais frequência.
    Devolve o dict do summary. Levanta CrowtadoError em falha.
    """
    from playwright.sync_api import sync_playwright
    from .registration_proxy import endpoint

    from .hostinger_mail import max_uid, wait_for_code

    summary: dict[str, Any] = {}
    eligibility: dict[str, Any] = {"checked": False}

    def _on_response(resp) -> None:
        try:
            if "externalMobileCapture.eligibilityStatus" in resp.url:
                eligibility.update(_normalize_eligibility(resp.json()))
                return
            if summary:
                return
            if "payouts.summary" in resp.url:
                body = resp.json()
                for item in (body if isinstance(body, list) else [body]):
                    data = ((item or {}).get("result") or {}).get("data") or {}
                    parsed = _summary_from_payload(data)
                    if parsed:
                        summary.update(parsed)
            elif "dashboard/earnings" in resp.url and "_rsc" in resp.url:
                summary.update(_extrai_summary(resp.text()))
        except Exception:  # noqa: BLE001 — resposta ilegível, segue o fluxo
            pass

    with sync_playwright() as pw:
        launch = {"channel": "chrome", "headless": not headed,
                  "args": ["--disable-blink-features=AutomationControlled"]}
        proxy = endpoint()
        try:
            browser = pw.chromium.launch(**launch)
        except Exception:  # noqa: BLE001 — sem Chrome instalado: cai p/ Chromium
            launch.pop("channel")
            browser = pw.chromium.launch(**launch)
        try:
            context_options = {"locale": "pt-BR"}
            if proxy:
                context_options["proxy"] = {"server": proxy}
            page = browser.new_context(**context_options).new_page()
            print("[*] abrindo sign-in do crowtado...")
            page.goto(SITE_BASE + "/pt-BR/sign-in", wait_until="domcontentloaded")

            page.wait_for_selector("#identifier-field", timeout=60_000)
            page.fill("#identifier-field", email)
            mailbox_error = None
            try:
                uid_base = max_uid(email)
            except Exception as exc:
                # A password-only account does not require a mailbox integration.
                # Keep the failure for a real email challenge, if one appears.
                uid_base = None
                mailbox_error = exc
            page.click("button.cl-formButtonPrimary")

            page.wait_for_selector("#password-field", timeout=60_000)
            page.fill("#password-field", senha)
            page.click("button.cl-formButtonPrimary")

            # Espera a transição: ou sai do sign-in (login direto), ou cai na
            # tela de 2º fator ("novo dispositivo", código por email).
            # Re-clica no botão se travar em factor-one (validação assíncrona).
            destino = None
            for _ in range(6):
                page.wait_for_timeout(5000)
                url = page.url
                if "/sign-in" not in url:
                    destino = "direto"
                    break
                if "factor-two" in url:
                    destino = "2fa"
                    break
                if page.query_selector("button.cl-formButtonPrimary"):
                    page.click("button.cl-formButtonPrimary")
            if not destino:
                raise CrowtadoError(
                    f"após a senha a página não avançou (url={page.url.split('?')[0]}): "
                    f"{page.inner_text('body')[:200]}"
                )
            print(f"[*] pós-senha: {destino} — {page.url.split('?')[0]}")

            if destino == "2fa":
                if mailbox_error is not None:
                    raise CrowtadoError(
                        "A Crowtado exige um código por e-mail, mas a caixa de entrada não pôde ser consultada.",
                        code="email_verification") from mailbox_error
                # 2FA por email: campo OTP único (data-input-otp) ou digit-N.
                page.wait_for_selector('input[data-input-otp], input[id^="digit-"]',
                                       timeout=30_000)
                print(f"[*] 2FA: aguardando código no email {email} ...")
                code = wait_for_code(email, sender="crowtado.com",
                                     min_uid=uid_base, timeout=180)
                print("[+] código de verificação recebido")
                if page.query_selector('input[id^="digit-"]'):
                    for i, digit in enumerate(code):
                        page.fill(f"#digit-{i}-field", digit)
                else:
                    page.locator('input[data-input-otp]').press_sequentially(code, delay=80)

            page.wait_for_url(lambda url: "/sign-in" not in url, timeout=60_000)
            print(f"[+] login OK — url: {page.url.split('?')[0]}")

            page.on("response", _on_response)
            page.goto(SITE_BASE + EARNINGS_PATH, wait_until="domcontentloaded")
            summary_waits = 0
            for _ in range(30):
                if summary:
                    summary_waits += 1
                if summary and (eligibility.get("checked") or summary_waits >= 5):
                    break
                page.wait_for_timeout(1000)

            if not summary:
                # fallback: flight payload embutido no HTML (streaming SSR)
                summary.update(_extrai_summary(page.content()))
        finally:
            browser.close()

    if not summary:
        raise CrowtadoError("não capturei o payouts.summary (layout/rede mudou?).")
    summary["contributorEligibility"] = eligibility
    return summary


def consultar_saldo(
        email: str, senha: str, headed: bool = True, *,
        fallback_browser: bool = True) -> dict[str, int]:
    """Consulta rápida por API e, opcionalmente, recorre ao navegador."""
    try:
        return consultar_saldo_api(email, senha)
    except CrowtadoError as api_error:
        if not fallback_browser or not can_use_browser_fallback(api_error):
            raise
        try:
            return consultar_saldo_navegador(email, senha, headed=headed)
        except Exception as browser_error:  # noqa: BLE001 — preserva os dois diagnósticos
            raise CrowtadoError(
                f"API falhou ({api_error}); navegador falhou ({browser_error})"
            ) from browser_error


# --- vínculo do email Minute (externalMobileCapture) ---------------------------

# Task "egocentric-household-mobile-latam" (fixa — é a única com capture externo).
TASK_ID_MINUTE = "78d06f56-16f7-449c-8e31-684a1dac6b3e"


def _trpc_error(proc: str, status: int, body: Any) -> CrowtadoError:
    item = body[0] if isinstance(body, list) and body else body
    error = item.get("error", {}) if isinstance(item, dict) else {}
    error = error.get("json", error) if isinstance(error, dict) else {}
    message = str(error.get("message", "")).casefold() if isinstance(error, dict) else ""
    # Classify known business errors without exposing the remote message,
    # which can echo an email, legal name or payment token.
    if status == 412 and ("demographics_required" in message
                         or message == "birth month, birth year, and gender are required"):
        return CrowtadoError("Complete nascimento e gênero na Crowtado (DEMOGRAPHICS_REQUIRED).",
                            code="demographics_required", http_status=status)
    if status == 412 and message == "minute_task_unavailable":
        return CrowtadoError("A tarefa Minute está indisponível na Crowtado. O cadastro foi preservado; retome quando a tarefa estiver disponível.",
                            code="minute_task_unavailable", http_status=status)
    if proc.startswith(("kyc.", "payouts.")):
        if "already linked to another" in message:
            return CrowtadoError("O destino já está vinculado a outra conta Crowtado.",
                                code="destination_in_use", http_status=status)
        if "pending payout" in message or "pending withdrawal" in message:
            return CrowtadoError("A Crowtado bloqueou a alteração porque há pagamento pendente.",
                                code="payout_pending", http_status=status)
    return _remote_error(f"Consulta Crowtado {proc} recusada", status, {})


def _site_trpc(sess: CrowtadoSession, proc: str, payload: dict[str, Any] | None,
               method: str = "POST") -> Any:
    """Chama /api/trpc/<proc> em www.crowtado.com com o JWT de sessão.

    O middleware do Clerk exige __session (JWT fresco) + __client_uat — o
    __client_uat já vem no cookie jar do login, então o JWT é injetado no jar
    (em vez de header Cookie manual, que o substituiria).
    tRPC batch de 1: POST {"0": {"json": payload}} / GET ?input={"0":{"json":...}}.
    Devolve o "json" da resposta ou levanta CrowtadoError.
    """
    jar = next(h.cookiejar for h in sess.opener.handlers
               if isinstance(h, urllib.request.HTTPCookieProcessor))
    jar.set_cookie(http.cookiejar.Cookie(
        version=0, name="__session", value=sess.session_jwt(),
        port=None, port_specified=False,
        domain="www.crowtado.com", domain_specified=True, domain_initial_dot=False,
        path="/", path_specified=True, secure=True, expires=None, discard=True,
        comment=None, comment_url=None, rest={}, rfc2109=False))
    if method == "POST":
        url = f"{SITE_BASE}/api/trpc/{proc}?batch=1"
        body = json.dumps({"0": {"json": payload}}).encode()
    else:
        qs = urllib.parse.quote(json.dumps({"0": {"json": payload}}))
        url = f"{SITE_BASE}/api/trpc/{proc}?batch=1&input={qs}"
        body = None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("User-Agent",
                   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
    req.add_header("Accept", "*/*")
    req.add_header("Origin", SITE_BASE)
    req.add_header("Referer", SITE_BASE + "/pt-BR/dashboard/tasks")
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with sess.opener.open(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        try:
            error_body = json.loads(exc.read().decode("utf-8", "replace"))
        except (ValueError, UnicodeError):
            error_body = None
        error = _trpc_error(proc, exc.code, error_body)
        if exc.code == 429:
            error.retry_after_seconds = _retry_after_seconds(exc.headers)
        raise error from exc
    except urllib.error.URLError as exc:
        code = "tls" if "CERTIFICATE_VERIFY_FAILED" in str(exc.reason).upper() else "network"
        raise CrowtadoError("Falha na conexão segura com Crowtado", code=code) from exc
    except TimeoutError as exc:
        raise CrowtadoError("Tempo esgotado ao consultar Crowtado", code="timeout") from exc
    except (ValueError, UnicodeError) as exc:
        raise CrowtadoError("Resposta inválida do Crowtado", code="invalid_response") from exc
    item = data[0] if isinstance(data, list) and data else data
    if isinstance(item, dict) and item.get("error"):
        error = item["error"]
        error = error.get("json", error) if isinstance(error, dict) else {}
        detail = error.get("data", {}) if isinstance(error, dict) else {}
        known_status = {"UNAUTHORIZED": 401, "FORBIDDEN": 403,
                        "TOO_MANY_REQUESTS": 429, "INTERNAL_SERVER_ERROR": 500}
        status = detail.get("httpStatus", known_status.get(detail.get("code"), 400)) if isinstance(detail, dict) else 400
        raise _trpc_error(proc, status if isinstance(status, int) else 400, item)
    result = item.get("result") if isinstance(item, dict) else None
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, dict):
        raise CrowtadoError("Resposta tRPC incompleta do Crowtado", code="invalid_response")
    return data.get("json", data)


# Gêneros aceitos pelo modal de demografia (gate obrigatório desde 26/08).
GENDERS = ("female", "male", "non_binary", "self_describe", "prefer_not_to_say")


def preencher_demografia(email: str, senha: str, birth_month: int, birth_year: int,
                         gender: str | None = None, *, session: CrowtadoSession | None = None) -> dict[str, Any]:
    """Preenche o gate obrigatório de demografia (mês/ano de nascimento + gênero).

    Sem isso o site responde 412 DEMOGRAPHICS_REQUIRED
    ("Birth month, birth year, and gender are required") em qualquer tRPC de
    task — inclusive externalMobileCapture.saveEmail/myStatus. Envia somente os
    dados fornecidos pelo titular e reutiliza a sessão quando informada.
    Devolve o demographicsStatus (state == "complete"). Levanta CrowtadoError.
    """
    if type(birth_month) is not int or not 1 <= birth_month <= 12:
        raise CrowtadoError(f"birth_month inválido: {birth_month}")
    now = datetime.now(timezone.utc)
    if (type(birth_year) is not int or not 1900 <= birth_year <= now.year
            or (birth_year == now.year and birth_month > now.month)):
        raise CrowtadoError("birth_year ou birth_month inválido para a data atual.")
    if gender is not None and gender not in GENDERS:
        raise CrowtadoError(f"gender inválido: {gender} (use um de {GENDERS})")
    sess = session if session is not None else login(email, senha)
    existing = _site_trpc(sess, "profile.demographicsStatus", None, method="GET")
    page = getattr(sess, "_registration_page", None)
    if isinstance(existing, dict) and (existing.get("state") == "complete" or existing.get("exempt") is True):
        if page is not None:
            _wait_for_registration_profile_continuation(page, email)
        return existing
    if (not isinstance(existing, dict) or not existing.get("state")
            or existing.get("state") == "underage_blocked"):
        raise CrowtadoError("A Crowtado não disponibilizou o preenchimento do perfil.", code="demographics_required")
    payload: dict[str, Any] = {"birthMonth": birth_month, "birthYear": birth_year}
    if gender:
        payload["gender"] = gender
    if page is not None:
        # Let the site's form update its query cache and perform its own
        # continuation. An API write followed by goto leaves this UI stale.
        _complete_registration_profile_page(page, email, birth_month, birth_year, gender)
    else:
        status = _site_trpc(sess, "profile.saveDemographics", payload)
        if not isinstance(status, dict) or status.get("state") != "complete":
            state = status.get("state") if isinstance(status, dict) else None
            code = "demographics_required" if state == "underage_blocked" else "invalid_response"
            raise CrowtadoError("Crowtado não confirmou a conclusão dos dados demográficos.", code=code)
    readback = _site_trpc(sess, "profile.demographicsStatus", None, method="GET")
    if (not isinstance(readback, dict)
            or (readback.get("state") != "complete" and readback.get("exempt") is not True)):
        raise CrowtadoError("Crowtado não confirmou os dados demográficos na leitura de retorno.",
                            code="invalid_response")
    return readback


def vincular_minute(email: str, senha: str, task_id: str = TASK_ID_MINUTE,
                    *, session: CrowtadoSession | None = None) -> Any:
    """Vincula o email do Minute na task do crowtado ('Conecte seu e-mail do Minute').

    Sem esse vínculo os envios do app Minute NÃO são creditados no crowtado.
    Consulta o estado antes de alterar. Quando necessário, usa o navegador da
    mesma sessão para passar pelos gates da tarefa e salvar o e-mail uma vez.
    Depois confirma lendo externalMobileCapture.myStatus.
    Devolve o myStatus (linkedAt, emailVerifiedAt, ...). Levanta CrowtadoError.
    """
    sess = session if session is not None else login(email, senha)

    def read_status():
        status = _site_trpc(sess, "externalMobileCapture.myStatus",
                            {"taskId": task_id, "locale": "pt-BR"}, method="GET")
        rows = status if isinstance(status, list) else [status]
        for row in rows:
            # The query is task-scoped; older responses omit the redundant ID.
            if not isinstance(row, dict) or row.get("taskId") not in (None, task_id):
                continue
            captured = str(row.get("capturedEmail") or "").strip().casefold()
            if captured and captured != email.strip().casefold():
                raise CrowtadoError("A tarefa já possui outro e-mail Minute salvo. Confira o vínculo antes de alterar.",
                                    code="minute_email_conflict")
            if captured == email.strip().casefold() and row.get("linkedAt"):
                if row.get("emailVerificationPending") is not False:
                    raise CrowtadoError("O e-mail foi salvo, mas a Crowtado ainda exige sua verificação.",
                                        code="email_verification")
                return status, True
        return status, False

    status, confirmed = read_status()
    if confirmed:
        return status
    if task_id != TASK_ID_MINUTE:
        raise CrowtadoError("A tarefa solicitada não corresponde ao fluxo de cadastro Minute.",
                            code="minute_task_unavailable")
    with _registration_task_page(sess) as page:
        _complete_minute_task_page(page, email)
    status, confirmed = read_status()
    if not confirmed:
        raise CrowtadoError("A Crowtado não confirmou o mesmo e-mail Minute na tarefa. Retome esta conta; o cadastro foi preservado.",
                            code="minute_link_unconfirmed")
    return status


# --- criação de conta (sign-up com Turnstile) ---------------------------------

def _chrome_exe() -> str:
    """Caminho do Chrome instalado. Erro claro se não achar."""
    import os
    import shutil
    import sys
    cands: list[str] = []
    if sys.platform == "win32":
        cands += [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]
    elif sys.platform == "darwin":
        cands.append("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    for cand in cands:
        if os.path.exists(cand):
            return cand
    for name in ("chrome", "google-chrome", "google-chrome-stable",
                 "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    pw = _playwright_chrome()
    if pw:
        return pw
    raise CrowtadoError("Chrome não encontrado — o sign-up precisa de um Chrome real.")


def _playwright_chrome() -> str | None:
    """Chromium privado do QMoney ou cache Playwright de desenvolvimento."""
    import glob
    import os
    import sys
    if sys.platform == "win32":
        roots = [
            os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
            str(config.RUNTIME_ROOT / "ms-playwright"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"),
        ]
        # Playwright mudou o diretório de chrome-win para chrome-win64 em
        # revisões recentes. O curinga mantém o pacote portátil compatível com
        # ambas sem depender de Chrome instalado no computador.
        for base in roots:
            if not base:
                continue
            matches = sorted(glob.glob(os.path.join(
                base, "chromium-*", "chrome-win*", "chrome.exe")), reverse=True)
            if matches:
                return matches[0]
        return None
    elif sys.platform == "darwin":
        pat = os.path.expanduser(
            "~/Library/Caches/ms-playwright/chromium-*/chrome-mac/"
            "Chromium.app/Contents/MacOS/Chromium")
    else:
        pat = os.path.expanduser(
            "~/.cache/ms-playwright/chromium-*/chrome-linux/chrome")
    matches = sorted(glob.glob(pat), reverse=True)
    return matches[0] if matches else None


def _wait_port(port: int, timeout: float = 30.0) -> None:
    import socket
    import time as _time
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return
        except OSError:
            _time.sleep(0.4)
    raise CrowtadoError(f"Chrome não abriu a porta de depuração {port}.")


def _registration_session_from_page(page, email: str) -> CrowtadoSession:
    """Reuse the authenticated signup session; never submit a second sign-in."""
    try:
        page.wait_for_function("() => Boolean(window.Clerk?.session && window.Clerk?.user)", timeout=30_000)
    except Exception:
        raise CrowtadoError(
            "O envio chegou à confirmação, mas a Crowtado não disponibilizou uma sessão autenticada. Preserve esta conta e confira o acesso antes de retomar.",
            code="signup_session_unconfirmed", phase="signup_confirmation", remote_effect_possible=True) from None
    identity = page.evaluate("""() => ({id: window.Clerk.session.id,
        email: window.Clerk.user.primaryEmailAddress?.emailAddress,
        banned: window.Clerk.user.banned, locked: window.Clerk.user.locked})""")
    if (not isinstance(identity, dict) or not identity.get("id")
            or str(identity.get("email") or "").strip().casefold() != email.strip().casefold()):
        raise CrowtadoError("O navegador não confirmou a sessão da conta solicitada.", code="authentication")
    _check_login_user_flags({"response": {"user": {key: value for key, value in identity.items()
                                                   if key in ("banned", "locked") and value is not None}}}, identity["id"])
    jar = http.cookiejar.CookieJar()
    for cookie in page.context.cookies():
        domain = cookie["domain"].lstrip(".")
        if domain != "crowtado.com" and not domain.endswith(".crowtado.com"):
            continue
        jar.set_cookie(http.cookiejar.Cookie(
            version=0, name=cookie["name"], value=cookie["value"], port=None, port_specified=False,
            domain=cookie["domain"], domain_specified=True, domain_initial_dot=cookie["domain"].startswith("."),
            path=cookie["path"], path_specified=True, secure=cookie["secure"], expires=None,
            discard=True, comment=None, comment_url=None, rest={}, rfc2109=False))
    session = CrowtadoSession(tls.build_opener(urllib.request.HTTPCookieProcessor(jar)), identity["id"])
    session._registration_page = page
    return session


def _check_registration_page_account(page, email: str) -> None:
    if not _same_site_origin(page.url):
        raise CrowtadoError("A continuação do cadastro perdeu a sessão Crowtado. Retome a mesma conta.",
                            code="signup_session_unconfirmed", remote_effect_possible=True)
    try:
        page.wait_for_function("() => Boolean(window.Clerk?.session && window.Clerk?.user)", timeout=30_000)
        actual = page.evaluate("() => window.Clerk.user.primaryEmailAddress?.emailAddress")
    except Exception:
        raise CrowtadoError("A Crowtado não confirmou a sessão da continuação do cadastro. Retome a mesma conta.",
                            code="signup_session_unconfirmed", remote_effect_possible=True) from None
    if str(actual or "").strip().casefold() != email.strip().casefold():
        raise CrowtadoError("O navegador não confirmou a mesma conta do cadastro. Nenhuma alteração foi enviada.",
                            code="authentication")


def _wait_for_registration_profile_continuation(page, email: str) -> None:
    """Wait for the profile's own router transition, without forcing a new URL."""
    try:
        page.wait_for_function(r"""() => {
            const profile = document.querySelector('[aria-labelledby="required-demographics-title"]');
            return /\/[A-Za-z]{2}(?:-[A-Za-z]{2})?\/dashboard(?:\/|$)/.test(location.pathname) &&
                (!profile || profile.getBoundingClientRect().height === 0);
        }""", timeout=30_000)
        _check_registration_page_account(page, email)
    except CrowtadoError:
        raise
    except Exception:
        raise CrowtadoError("A Crowtado não confirmou a continuação do perfil na sessão do cadastro. Retome a mesma conta.",
                            code="signup_session_unconfirmed", remote_effect_possible=True) from None


def _complete_registration_profile_page(page, email: str, month: int, year: int,
                                        gender: str | None) -> None:
    """Submit the actual required-profile form in the authenticated signup tab."""
    submitted = False
    try:
        _check_registration_page_account(page, email)
        dialog = page.locator('[role="dialog"][aria-labelledby="required-demographics-title"]')
        dialog.wait_for(state="visible", timeout=30_000)
        month_field = dialog.locator('select[autocomplete="bday-month"]')
        year_field = dialog.locator('input[autocomplete="bday-year"]')
        month_field.select_option(str(month), timeout=10_000)
        year_field.fill(str(year), timeout=10_000)
        if gender is not None:
            dialog.locator(f'input[type="radio"][name="gender"][value="{gender}"]').check(timeout=10_000)
        if (month_field.input_value() != str(month) or year_field.input_value() != str(year)
                or gender is not None and not dialog.locator(
                    f'input[type="radio"][name="gender"][value="{gender}"]').is_checked()):
            raise CrowtadoError("O formulário não confirmou os dados de perfil informados pelo titular.",
                                code="demographics_required", remote_effect_possible=False)
        submit = dialog.locator('button[type="submit"]')
        submit.click(trial=True, timeout=10_000)
        _check_registration_page_account(page, email)
        submitted = True
        with page.expect_response(lambda response: _same_site_origin(response.url)
                                  and urllib.parse.urlsplit(response.url).path == "/api/trpc/profile.saveDemographics"
                                  and response.request.method == "POST", timeout=30_000) as pending:
            submit.click(timeout=10_000)
        response = pending.value
        if response.status >= 400:
            error = _trpc_error("profile.saveDemographics", response.status, None)
            if response.status == 429:
                error.retry_after_seconds = _retry_after_seconds(response.headers)
            error.remote_effect_possible = True
            raise error
        _wait_for_registration_profile_continuation(page, email)
    except CrowtadoError:
        raise
    except Exception:
        raise CrowtadoError("Não foi possível confirmar o formulário de perfil na aba do cadastro. Retome a mesma conta; o formulário não foi reenviado.",
                            code="demographics_required", remote_effect_possible=submitted) from None


@contextmanager
def _registration_task_page(session):
    """Keep signup's browser, or attach an API login to a temporary browser once."""
    page = getattr(session, "_registration_page", None)
    if page is not None:
        yield page
        return
    import socket
    import subprocess
    import tempfile
    from playwright.sync_api import sync_playwright
    from .registration_proxy import endpoint

    jar = next(handler.cookiejar for handler in session.opener.handlers
               if isinstance(handler, urllib.request.HTTPCookieProcessor))
    cookies = [{"name": cookie.name, "value": cookie.value, "domain": cookie.domain,
                "path": cookie.path, "secure": cookie.secure} for cookie in jar
               if cookie.domain.lstrip(".") == "crowtado.com" or cookie.domain.endswith(".crowtado.com")]
    cookies = [cookie for cookie in cookies if cookie["name"] != "__session"]
    cookies.append({"name": "__session", "value": session.session_jwt(),
                    "domain": "www.crowtado.com", "path": "/", "secure": True})
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="qmoney-registration-") as profile:
        proxy = endpoint()
        args = [f"--proxy-server={proxy}"] if proxy else []
        process = subprocess.Popen([_chrome_exe(), f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", *args, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            _wait_port(port)
            with sync_playwright() as pw:
                browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                context = browser.contexts[0]
                context.add_cookies(cookies)
                try:
                    yield context.new_page()
                finally:
                    try:
                        browser.new_browser_cdp_session().send("Browser.close")
                    except Exception:
                        pass
                    browser.close()
        finally:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def _complete_minute_task_page(page, email: str) -> None:
    """Follow the actual task gates, with one click per confirmed transition."""
    try:
        # A resumed API session starts in a blank tab. A live signup must
        # continue through the site's links, preserving its router/session.
        if page.url == "about:blank":
            page.goto(SITE_BASE + "/pt-BR/dashboard/tasks",
                      wait_until="domcontentloaded", timeout=30_000)
        _check_registration_page_account(page, email)
        path = urllib.parse.urlsplit(page.url).path.rstrip("/")
        if not re.fullmatch(r"/[A-Za-z]{2}(?:-[A-Za-z]{2})?/dashboard(?:/.*)?", path):
            raise CrowtadoError("O perfil ainda não concluiu a continuação no cadastro. Retome a mesma conta.",
                                code="signup_session_unconfirmed", remote_effect_possible=True)
        locale = path.split("/", 2)[1]
        task_path = f"/{locale}/dashboard/tasks/egocentric-household-mobile-latam"
        if path != task_path:
            task_link = page.locator(f'a[href="{task_path}"], a[href="{SITE_BASE}{task_path}"]')
            if not task_link.count():
                tasks_path = f"/{locale}/dashboard/tasks"
                if path != tasks_path:
                    tasks_link = page.locator(f'a[href="{tasks_path}"], a[href="{SITE_BASE}{tasks_path}"]').first
                    tasks_link.click(timeout=30_000)
                task_link.wait_for(state="visible", timeout=30_000)
            task_link.first.click(timeout=30_000)
            page.wait_for_function("path => location.pathname.replace(/\\/$/, '') === path",
                                   arg=task_path, timeout=30_000)
        page.wait_for_function("""() => /Prossiga|Esta tarefa exige um suporte de cabeça|E-mail usado no Minute/.test(document.body.innerText)""",
                               timeout=30_000)
        _check_registration_page_account(page, email)
        proceed = page.get_by_role("button", name="Prossiga", exact=True)
        if not proceed.count():
            proceed = page.get_by_role("link", name="Prossiga", exact=True)
        if proceed.count() and proceed.first.is_visible():
            proceed.first.click(timeout=10_000)
            page.wait_for_function("""() => /Esta tarefa exige um suporte de cabeça|E-mail usado no Minute/.test(document.body.innerText)""",
                                   timeout=30_000)
        headmount = page.get_by_role("button", name=re.compile(r"Não,?\s+já tenho um", re.I))
        if headmount.count() and headmount.first.is_visible():
            headmount.first.click(timeout=10_000)
        field = page.get_by_label("E-mail usado no Minute", exact=True)
        field.wait_for(state="visible", timeout=30_000)
        if field.input_value().strip().casefold() != email.strip().casefold():
            field.fill(email, timeout=10_000)
        if field.input_value().strip().casefold() != email.strip().casefold():
            raise CrowtadoError("O campo de e-mail Minute não confirmou o preenchimento.", code="minute_link_unconfirmed")
        save = page.get_by_role("button", name="Salvar e-mail", exact=True)
        if save.count() and save.first.is_enabled():
            with page.expect_response(lambda response: "externalMobileCapture.saveEmail" in response.url
                                      and response.request.method == "POST", timeout=30_000) as pending:
                save.first.click(timeout=10_000)
            response = pending.value
            if response.status >= 400:
                error = _trpc_error("externalMobileCapture.saveEmail", response.status, None)
                if response.status == 429:
                    error.retry_after_seconds = _retry_after_seconds(response.headers)
                raise error
        elif not page.get_by_text("E-mail salvo", exact=True).count():
            raise CrowtadoError("A tarefa não disponibilizou a confirmação do e-mail Minute.", code="minute_link_unconfirmed")
    except CrowtadoError:
        raise
    except Exception:
        raise CrowtadoError("Não foi possível confirmar a etapa da tarefa. Retome esta conta; nenhum clique será repetido nesta tentativa.",
                            code="minute_link_unconfirmed", remote_effect_possible=True) from None


def criar_conta(email: str, senha: str, ref: str = DEFAULT_REF,
                timeout_email: int = 180, *, on_progress: Any = None,
                on_authenticated: Any = None) -> None:
    """Cria uma conta no crowtado (sign-up + verificação de email automática).

    O sign-up tem Cloudflare Turnstile (via Clerk) — só passa num Chrome REAL,
    então lançamos o Chrome com `--remote-debugging-port` (perfil persistente
    em CHROME_PROFILE) e dirigimos via CDP. O código de verificação é lido da
    caixa catch-all da Hostinger (hostinger_mail.wait_for_code), então o email
    precisa ser de um domínio catch-all configurado (ex.: @academy4u.com.br).

    CAPTCHA aguarda intervenção no navegador por até cinco minutos. O callback
    opcional recebe captcha/submitting/confirmation/email e pode interromper a
    operação lançando uma exceção. Não recebe credenciais ou conteúdo da página.
    on_authenticated continua o fluxo na sessão existente antes de fechar o
    navegador; não solicita um novo login nem outro código de autenticação.
    Retorna None somente após o redirecionamento oficial de confirmação.
    """
    import subprocess
    from .registration_proxy import endpoint
    from .human_behavior import (
        human_click,
        random_delay,
    )
    from .atomic_io import JsonStateError

    from playwright.sync_api import sync_playwright

    from .hostinger_mail import max_uid, wait_for_code

    phase = "browser_setup"
    remote_effect_possible = False
    captcha_deadline = None

    def _progress(event):
        if callable(on_progress):
            on_progress(event)

    def _check_page(page):
        if signup_response_error is not None:
            raise signup_response_error
        if page.is_closed():
            raise CrowtadoError(
                "O navegador do cadastro foi fechado. O progresso foi preservado; confira e retome a mesma conta.",
                code="signup_browser_closed")

    def _destination(page):
        nonlocal remote_effect_possible
        _check_page(page)
        if _is_signup_verification_url(page.url):
            remote_effect_possible = True
            return "verify"
        if _is_signup_confirmation_url(page.url):
            remote_effect_possible = True
            return "direto"
        if remote_effect_possible and _is_signup_session_transfer_url(page.url):
            return None  # Clerk may still be synchronizing its new session.
        if not _is_signup_submission_url(page.url):
            raise CrowtadoError("O redirecionamento não confirmou o cadastro no Crowtado.",
                                code="signup_unconfirmed")
        return None

    def _wait_for_captcha(page):
        nonlocal captcha_deadline
        while True:
            if _destination(page) == "direto":
                return
            # Only read public DOM state. A filled response allows a normal
            # submit; it is never treated as acceptance by the provider.
            pending = page.evaluate("""() => {
                const visible = el => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 &&
                        style.display !== 'none' && style.visibility !== 'hidden';
                };
                const challenge = [...document.querySelectorAll(
                    'iframe[src*="challenges.cloudflare.com"], .cf-turnstile, #clerk-captcha'
                )].some(visible);
                const response = [...document.querySelectorAll(
                    'input[name="cf-turnstile-response"], input[name="clerk-captcha-token"]'
                )].some(el => Boolean(el.value.trim()));
                return challenge && !response;
            }""")
            if not pending:
                return
            if captcha_deadline is None:
                captcha_deadline = time.monotonic() + 300
                page.bring_to_front()
            _progress("captcha")
            if time.monotonic() >= captcha_deadline:
                raise CrowtadoError(
                    "A verificação CAPTCHA não foi concluída em cinco minutos. O lote foi interrompido; confira e retome a mesma conta.",
                    code="captcha_required")
            page.wait_for_timeout(500)

    def _wait_for_destination(page, timeout, *, confirmation_only=False):
        deadline = time.monotonic() + timeout
        while True:
            destination = _destination(page)
            if destination == "direto" or (destination == "verify" and not confirmation_only):
                return destination
            started = time.monotonic()
            _wait_for_captcha(page)
            # Interactive verification has its own bounded budget and must not
            # consume the ordinary response deadline.
            deadline += time.monotonic() - started
            _progress("confirmation")
            if _destination(page) == "direto":
                return "direto"
            if not confirmation_only and _destination(page) == "verify":
                return "verify"
            invalid = page.evaluate("""() => [...document.querySelectorAll(
                '.cl-formFieldErrorText, .cl-alertText'
            )].some(el => el.getBoundingClientRect().height > 0 && Boolean(el.textContent.trim()))""")
            if invalid:
                raise CrowtadoError("O formulário Crowtado informou um erro. Confira os dados e a mesma conta no site.",
                                    code="signup_form_invalid")
            if time.monotonic() >= deadline:
                if _is_signup_session_transfer_url(page.url):
                    raise CrowtadoError("O código de e-mail foi enviado, mas a Crowtado voltou à entrada de login sem confirmar a sessão. Retome a mesma conta; nenhum novo login ou cadastro foi enviado.",
                                        code="signup_session_unconfirmed", phase="signup_confirmation",
                                        remote_effect_possible=True)
                raise CrowtadoError("O site não confirmou o cadastro dentro do prazo. Retome a mesma conta para verificar o acesso; nenhum envio foi repetido.",
                                    code="signup_unconfirmed")
            page.wait_for_timeout(500)

    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    # O cadastro já abriu o túnel da conta. O Chrome só recebe esse endereço local.
    proxy = endpoint()
    proxy_args = [f"--proxy-server={proxy}", "--disable-quic"] if proxy else []

    def _type_signup_field(page, selector: str, value: str) -> None:
        try:
            # Replace autofilled/stale text and verify the actual input, rather
            # than assuming that keystrokes reached the intended field.
            page.fill(selector, value, timeout=10_000)
            if page.input_value(selector, timeout=10_000) == value:
                return
        except Exception:
            pass
        if signup_response_error is not None:
            raise signup_response_error
        field = "e-mail" if selector == "#emailAddress-field" else "senha"
        raise CrowtadoError(
            f"O campo de {field} do Crowtado não confirmou o preenchimento. Confira os dados e retome a mesma conta.",
            code="signup_field_unconfirmed", remote_effect_possible=remote_effect_possible)

    def _click_signup(page, selector: str) -> None:
        nonlocal remote_effect_possible
        _check_page(page)
        if remote_effect_possible or _destination(page) is not None:
            return
        # A coordinate click can silently miss or hit a disabled button. Trial
        # waits for actionability without submitting or bypassing the page gate.
        try:
            page.click(selector, trial=True, timeout=10_000)
        except Exception:
            if signup_response_error is not None:
                raise signup_response_error
            if remote_effect_possible and _destination(page) is not None:
                return
            raise CrowtadoError(
                "O botão de cadastro Crowtado não ficou disponível. Confira os campos, o aceite e as verificações indicadas no site; nenhum clique automático foi realizado.",
                code="signup_not_ready", remote_effect_possible=remote_effect_possible) from None
        if signup_response_error is not None:
            raise signup_response_error
        if remote_effect_possible or _destination(page) is not None:
            return  # The user may have submitted while completing verification.
        _progress("submitting")
        # From this point a failed click may have submitted before losing its
        # response. Never fall back to a second click in that uncertain state.
        remote_effect_possible = True
        try:
            page.click(selector, timeout=10_000)
        except Exception:
            if signup_response_error is not None:
                raise signup_response_error
            raise CrowtadoError(
                "Não foi possível confirmar o envio do formulário Crowtado. Retome a mesma conta para verificar o acesso; o clique não foi repetido.",
                code="signup_click_unconfirmed", remote_effect_possible=True) from None

    proc = None
    try:
        try:
            proc = subprocess.Popen(
                [_chrome_exe(), f"--remote-debugging-port={port}",
                 f"--user-data-dir={CHROME_PROFILE}", "--no-first-run",
                 "--no-default-browser-check", *proxy_args, "about:blank"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            raise CrowtadoError("Não foi possível iniciar o navegador de cadastro.",
                                code="signup_browser_unavailable") from None
        _wait_port(port)
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            ctx = browser.contexts[0]
            ctx.clear_cookies()  # sessão da conta anterior (batch) não vaza
            # Keep the installed browser's native identity and environment.
            # Signup must not inject fabricated device attributes.
            page = ctx.new_page()

            signup_response_error: CrowtadoError | None = None
            clerk_signup_response_error: CrowtadoError | None = None

            def observe_signup_request(request) -> None:
                nonlocal remote_effect_possible
                if request.method == "POST" and _is_clerk_signup_url(request.url):
                    remote_effect_possible = True

            def observe_signup_response(response) -> None:
                nonlocal signup_response_error, clerk_signup_response_error
                try:
                    # A Clerk signup rejection carries more specific evidence
                    # than a proof endpoint failure, regardless of event order.
                    clerk_candidate = _signup_response_error_from_response(response, phase)
                    if clerk_candidate is None:
                        clerk_candidate = _signup_session_response_error(response, phase)
                    if clerk_candidate is not None:
                        clerk_signup_response_error = clerk_candidate
                        signup_response_error = clerk_candidate
                        return
                    proof_phase = phase if remote_effect_possible else "signup_preparation"
                    proof_candidate = _signup_proof_response_error_from_response(
                        response, phase=proof_phase,
                        remote_effect_possible=remote_effect_possible)
                    if proof_candidate is not None and clerk_signup_response_error is None:
                        signup_response_error = proof_candidate
                except Exception:
                    # Response bodies can contain user data. A diagnostic hook
                    # must never retain or surface them if parsing fails.
                    return

            page.on("response", observe_signup_response)
            page.on("request", observe_signup_request)
            signup_url = f"{SITE_BASE}/sign-up"
            if ref:
                signup_url += "?" + urllib.parse.urlencode({"ref": ref})
            page.goto(signup_url,
                      wait_until="domcontentloaded", timeout=90_000)
            page.wait_for_timeout(4000)
            if signup_response_error is not None:
                raise signup_response_error
            if not _is_signup_submission_url(page.url):
                raise CrowtadoError("O site não permaneceu na rota oficial de cadastro.",
                                    code="signup_unconfirmed", phase=phase,
                                    remote_effect_possible=False)

            # The terms gate can disable the form through an ancestor while
            # the inputs themselves still report disabled=False. A coordinate
            # click may miss an off-screen checkbox; check and verify the
            # specific terms control before attempting to fill the form.
            consent = page.locator("#signup-consent")
            if consent.count():
                try:
                    consent.check(timeout=10_000)
                    if not consent.is_checked():
                        raise RuntimeError("consent not checked")
                except Exception:
                    if signup_response_error is not None:
                        raise signup_response_error
                    raise CrowtadoError(
                        "Não foi possível confirmar o aceite dos termos do cadastro. Confira a caixa de aceite no site; nenhum formulário foi enviado.",
                        code="signup_not_ready", remote_effect_possible=remote_effect_possible) from None
            random_delay(1000, 2000, page=page)

            if consent.count():
                # The current page renders the terms before Clerk is ready.
                # Do not mistake its loading state for the older CTA screen.
                try:
                    page.wait_for_selector("#emailAddress-field", timeout=30_000)
                except Exception:
                    _check_page(page)
                    raise CrowtadoError(
                        "O formulário de cadastro não terminou de carregar. Nenhum cadastro foi enviado; confira a página antes de retomar.",
                        code="signup_not_ready", remote_effect_possible=remote_effect_possible) from None

            # o form Clerk fica atrás do aceite dos termos + CTA custom do site
            if not page.query_selector("#emailAddress-field"):
                clicked = False
                for label in (
                    "Crie sua conta",
                    "Cadastre-se como Colaborador",
                    "Create account",
                    "Sign up as a Contributor",
                ):
                    loc = page.get_by_role("button", name=label)
                    if loc.count() == 0:
                        loc = page.get_by_text(label, exact=False)
                    if loc.count() == 0:
                        continue
                    try:
                        human_click(page, f"button:has-text('{label}')")
                        clicked = True
                        break
                    except Exception:
                        try:
                            loc.click(timeout=5_000)
                            clicked = True
                            break
                        except Exception:
                            continue
                if not clicked:
                    raise CrowtadoError(
                        "CTA de cadastro não apareceu "
                        "(esperado 'Crie sua conta' / 'Cadastre-se como Colaborador')"
                    )
            page.wait_for_selector("#emailAddress-field", timeout=30_000)

            # Snapshot the inbox before any interactive wait: the user may
            # submit manually while the browser is open.
            uid_base = max_uid(email)
            if (not page.is_enabled("#emailAddress-field")
                    or not page.is_enabled("#password-field")):
                _wait_for_captcha(page)
            if not remote_effect_possible and _destination(page) is None:
                random_delay(500, 1500, page=page)
                _type_signup_field(page, "#emailAddress-field", email)
                random_delay(800, 1500, page=page)
                _type_signup_field(page, "#password-field", senha)
                random_delay(1000, 2000, page=page)

            # Respect the public frontend's proof gate. A reported proof
            # failure stops before the Clerk sign-up form is submitted.
            if signup_response_error is not None:
                raise signup_response_error
            phase = "signup_submission"

            _wait_for_captcha(page)
            if not remote_effect_possible and _destination(page) is None:
                # User interaction/autofill during a challenge can change fields.
                if (page.input_value("#emailAddress-field") != email
                        or page.input_value("#password-field") != senha):
                    raise CrowtadoError("Os dados do formulário mudaram durante a verificação. Nenhum envio automático foi iniciado.",
                                        code="signup_field_unconfirmed")
                _click_signup(page, "button.cl-formButtonPrimary")
            destino = _wait_for_destination(page, 60)

            if destino == "verify":
                phase = "email_verification"
                _progress("email")
                try:
                    code = wait_for_code(email, sender="crowtado.com",
                                         min_uid=uid_base, timeout=timeout_email)
                except Exception:
                    raise CrowtadoError("Não foi possível obter o código de e-mail do cadastro. Confira e retome a mesma conta; nenhum cadastro será repetido.",
                                        code="signup_email_unconfirmed") from None
                _check_page(page)
                if not isinstance(code, str) or re.fullmatch(r"[0-9]{6}", code) is None:
                    raise CrowtadoError("O código de verificação recebido tem formato inválido.",
                                        code="signup_email_unconfirmed")
                if page.query_selector('input[id^="digit-"]'):
                    for i, digit in enumerate(code):
                        page.fill(f"#digit-{i}-field", digit)
                else:
                    page.locator('input[data-input-otp]').press_sequentially(code, delay=80)

            # sucesso = saiu do fluxo de sign-up (cai no dashboard)
            phase = "signup_confirmation"
            _wait_for_destination(page, 60, confirmation_only=True)
            if callable(on_authenticated):
                try:
                    authenticated_session = _registration_session_from_page(page, email)
                except Exception:
                    # A provider restriction observed during the wait is more
                    # informative than the browser's timeout or missing session.
                    _check_page(page)
                    raise
                _check_page(page)
                on_authenticated(authenticated_session)
            browser.close()
    except (OSError, JsonStateError):
        # A failed durable progress callback is not a provider rejection.
        raise
    except CrowtadoError as exc:
        if exc.phase is None:
            exc.phase = phase
        if exc.remote_effect_possible is None:
            exc.remote_effect_possible = remote_effect_possible
        raise
    except Exception as exc:  # noqa: BLE001 — Playwright quebra de N jeitos
        raise CrowtadoError("Não foi possível concluir o cadastro do Crowtado.",
                            code="signup_error", phase=phase,
                            remote_effect_possible=remote_effect_possible) from None
    finally:
        # terminate() só solicita o encerramento. Aguarde a liberação do
        # perfil antes de permitir o cadastro seguinte usar o mesmo diretório.
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
