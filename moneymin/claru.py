"""Claru login and verified partner identity, separate from Minute credentials.

Endpoints match the official app client. Only sign-in, own profile and own
inbox address are used. Cookies remain in memory; passwords stay in DPAPI.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from contextlib import ExitStack
import threading
import requests

from . import config, credential_store, secure_store, registration_proxy

BASE = "https://app.claru.ai"
_LOCK = threading.RLock()


class ClaruError(RuntimeError):
    pass


class ClaruActionRequired(ClaruError):
    """The account exists, but its owner must complete the official site flow."""


def session_route(session):
    session.trust_env = False
    endpoint = registration_proxy.endpoint()
    if endpoint:
        session.proxies = {"http": endpoint, "https": endpoint}


def confirmed_profile(session, email):
    profile = _json(session, "/api/v1/users/me")
    if credential_store.email_key(profile.get("email")) != email or profile.get("enabled") is not True:
        raise ClaruError("O Claru não confirmou a identidade e o acesso ativo desta conta.")
    if not profile.get("inboxAddress"):
        raise ClaruActionRequired("A conta Claru existe. Conclua o perfil e os termos no site Claru e retome esta mesma conta.")
    inbox = _json(session, "/api/v1/inbox/address")
    minute = _minute_email(inbox.get("address"))
    if _minute_email(profile.get("inboxAddress")) != minute:
        raise ClaruError("Perfil e caixa Claru retornaram e-mails diferentes. Nenhum cadastro Minute foi iniciado.")
    return profile, minute


def save_connection(email, password, profile, minute, *, expected_minute=None):
    with _LOCK:
        rows = _load()
        saved = rows.get(email)
        expected = expected_minute or (saved or {}).get("minute_email")
        if expected is not None and _minute_email(expected) != minute:
            raise ClaruError("O e-mail gerado mudou no Claru. Consulte o perfil novamente antes de conectar o Minute.")
        if any(r["minute_email"] == minute and owner != email for owner, r in rows.items()):
            raise ClaruError("Este e-mail Minute já está associado a outro login Claru. Preserve as conexões existentes.")
        name = " ".join(profile.get(k, "") for k in ("firstName", "lastName") if isinstance(profile.get(k), str))[:200]
        row = {"login_email": email, "minute_email": minute, "password": password,
               "name": name, "checked_at": datetime.now(timezone.utc).isoformat()}
        rows[email] = row
        secure_store.save_secure_settings(config.SECRETS_DIR / "claru_accounts.dat", {"schema": 1, "accounts": rows})
        return {k: v for k, v in row.items() if k != "password"}


def _minute_email(value):
    email = credential_store.email_key(value)
    if email.rsplit("@", 1)[1] != "supply.claru.ai":
        raise ClaruError("O perfil Claru não confirmou um e-mail @supply.claru.ai válido.")
    return email


def _load():
    value = secure_store.load_secure_settings(config.SECRETS_DIR / "claru_accounts.dat", strict=True)
    if not value:
        return {}
    if set(value) != {"schema", "accounts"} or type(value["schema"]) is not int or value["schema"] != 1 or not isinstance(value["accounts"], dict):
        raise ClaruError("O cofre Claru está inválido. Preserve o arquivo antes de continuar.")
    found = set()
    for email, row in value["accounts"].items():
        try:
            valid = (credential_store.email_key(email) == email and isinstance(row, dict)
                     and set(row) == {"login_email", "minute_email", "password", "name", "checked_at"}
                     and row["login_email"] == email and _minute_email(row["minute_email"]) == row["minute_email"]
                     and isinstance(row["password"], str) and 0 < len(row["password"]) <= 4096
                     and isinstance(row["name"], str) and isinstance(row["checked_at"], str)
                     and row["minute_email"] not in found)
        except (ValueError, ClaruError):
            valid = False
        if not valid:
            raise ClaruError("O cofre Claru está inválido. Preserve o arquivo antes de continuar.")
        found.add(row["minute_email"])
    return value["accounts"]


def public_accounts():
    with _LOCK:
        return [{k: v for k, v in row.items() if k != "password"} for row in _load().values()]


def account_for_minute(email):
    email = _minute_email(email)
    with _LOCK:
        row = next((r for r in _load().values() if r["minute_email"] == email), None)
        if row is None:
            raise ClaruError("Conecte primeiro a conta Claru para confirmar o e-mail gerado do Minute.")
        return {k: v for k, v in row.items() if k != "password"}


def _json(session, path, *, body=None):
    try:
        response = session.request("POST" if body is not None else "GET", BASE + path,
                                   json=body, timeout=30, allow_redirects=False,
                                   headers={"Origin": BASE, "Referer": BASE + "/users/profile"})
        if response.status_code not in (200, 201):
            if response.status_code in (401, 403):
                raise ClaruError("O Claru recusou o acesso. Confira o login ou a situação da conta no site.")
            raise ClaruError(f"Consulta Claru não concluída (HTTP {response.status_code}). Nenhum cadastro Minute foi iniciado.")
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (requests.RequestException, ValueError):
        # Never echo remote bodies, cookies, credentials or request details.
        raise ClaruError("Não foi possível confirmar a resposta do Claru. Confira a conexão e tente novamente.") from None


def connect(email, password=None, *, expected_minute=None):
    email = credential_store.email_key(email)
    with _LOCK:
        rows = _load()
        saved = rows.get(email)
        if password is None and saved:
            password = saved["password"]
        if not isinstance(password, str) or not password or len(password) > 4096:
            raise ValueError("Informe a senha do Claru para consultar o perfil.")
        with requests.Session() as session:
            session_route(session)
            _json(session, "/api/v1/auth/app-sign-in", body={"email": email, "password": password})
            profile, minute = confirmed_profile(session, email)
        return save_connection(email, password, profile, minute, expected_minute=expected_minute)


def verify_minute(email):
    row = account_for_minute(email)
    return connect(row["login_email"], expected_minute=email)


BALANCE_FIELDS = {"provider", "currency", "availableCents", "withdrawableCents", "heldHourlyCents",
                  "submissionCents", "referralCents", "bonusCents", "pendingNotProvided"}


def _cents(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ClaruError("O Claru não confirmou um valor de saldo válido.")
    try:
        amount = Decimal(str(value)) * 100
        if not amount.is_finite() or amount < 0 or amount > 2**53 - 1 or amount != amount.to_integral_value():
            raise ValueError
        return int(amount)
    except (InvalidOperation, ValueError):
        raise ClaruError("O Claru não confirmou um valor de saldo válido.") from None


def validate_balance(summary):
    if (not isinstance(summary, dict) or set(summary) - BALANCE_FIELDS
            or summary.get("provider") != "claru" or summary.get("currency") != "USD"
            or summary.get("pendingNotProvided") is not True
            or any(type(summary.get(k)) is not int or not 0 <= summary[k] <= 2**53 - 1
                   for k in ("availableCents", "submissionCents", "referralCents", "bonusCents"))
            or any(type(summary[k]) is not int or not 0 <= summary[k] <= 2**53 - 1
                   for k in ("withdrawableCents", "heldHourlyCents") if k in summary)
            or sum(summary[k] for k in ("submissionCents", "referralCents", "bonusCents")) != summary["availableCents"]):
        raise ClaruError("O resumo de saldo Claru está incompleto. Consulte novamente.")
    return dict(summary)


def balance_from_payload(payload):
    if not isinstance(payload, dict) or payload.get("amountsHidden") is not False:
        raise ClaruError("O Claru não disponibilizou os valores deste perfil. Confira os ganhos no site.")
    available = _cents(payload.get("amount"))
    fixed = payload.get("fixedRewards", {})
    if not isinstance(fixed, dict) or not isinstance(fixed.get("rewards", []), list):
        raise ClaruError("O Claru retornou um resumo de ganhos incompleto.")
    bonus = referral = 0
    for reward in fixed.get("rewards", []):
        if not isinstance(reward, dict) or reward.get("source") not in {"REFERRAL", "MILESTONE", "AFFILIATE_COMMISSION", "BUSINESS_REFERRAL", "HOURLY_REFERRAL"}:
            raise ClaruError("O Claru retornou uma categoria de ganho não reconhecida.")
        if reward["source"] == "MILESTONE":
            bonus += _cents(reward.get("amount"))
        else:
            referral += _cents(reward.get("amount"))
    result = {"provider": "claru", "currency": "USD", "availableCents": available,
              "submissionCents": available - bonus - referral, "referralCents": referral,
              "bonusCents": bonus, "pendingNotProvided": True}
    if payload.get("withdrawableAmount") is not None:
        result["withdrawableCents"] = _cents(payload["withdrawableAmount"])
    if "withdrawalHeldHourly" in payload:
        held = payload["withdrawalHeldHourly"]
        if not isinstance(held, dict) or held.get("reason") != "stale_source":
            raise ClaruError("O Claru retornou uma retenção não reconhecida.")
        result["heldHourlyCents"] = _cents(held.get("amount"))
    return validate_balance(result)


def balance(email):
    email = _minute_email(email)
    with _LOCK:
        saved = next((r.copy() for r in _load().values() if r["minute_email"] == email), None)
    if saved is None:
        raise ClaruError("Conecte o perfil Claru desta conta antes de consultar o saldo.")
    with ExitStack() as scope:
        scope.enter_context(registration_proxy.route(registration_proxy.assign(email, "")))
        session = scope.enter_context(requests.Session())
        session_route(session)
        _json(session, "/api/v1/auth/app-sign-in", body={"email": saved["login_email"], "password": saved["password"]})
        profile, minute = confirmed_profile(session, saved["login_email"])
        if minute != email:
            raise ClaruError("O e-mail gerado do Claru mudou. Confira a conexão antes de consultar o saldo.")
        summary = _json(session, "/api/v1/payments/summary/me")
    return balance_from_payload(summary)
