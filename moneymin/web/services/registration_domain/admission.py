"""Account-domain implementation; invoked with explicit dependency bindings."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('AuthError', 'JsonStateError', '_INTEGRATION_OPERATION_LOCK', '_PERSISTENCE_LOCK', '_apply_hostinger', '_hostinger_profiles', '_integration_error', '_migrate_legacy_integrations', '_step_fail', 'config', 'hostinger_mail', 'math', 'registration_limits', 'registration_state', 'save_secure_settings', 'time', 'unicodedata', 'uuid')


def _registration_failure(_services, exc, *, provider=None, detail=None):
    result = _services._step_fail(str(exc) if detail is None else detail)
    code = getattr(exc, "account_issue_code", None)
    if isinstance(code, str):
        result["code"] = code
    effect = getattr(exc, "remote_effect_possible", None)
    if type(effect) is bool:
        result["remote_effect_possible"] = effect
    for key in ("retry_after_seconds", "http_status"):
        value = getattr(exc, key, None)
        if type(value) in (int, float) and _services.math.isfinite(value) and value >= 0:
            result[key] = _services.math.ceil(value)
    if "retry_after_seconds" in result:
        result["retry_at"] = int(_services.time.time()) + result["retry_after_seconds"]
    if code == "rate_limit":
        provider = provider or ("minute" if isinstance(exc, _services.AuthError) else "crowtado")
        wait = result.get("retry_after_seconds", _services.registration_limits.DEFAULT_COOLDOWN_SECONDS)
        result["retry_after_seconds"] = wait
        result["retry_at"] = _services.math.ceil(_services.time.time()) + wait
        try:
            wait = _services.registration_limits.defer(provider, wait)
        except (OSError, _services.JsonStateError):
            # Preserve the observed deadline in this account's durable
            # checkpoint as well. The limits module also retains it in RAM
            # if the filesystem cannot confirm either publication.
            result["detail"] += " Não foi possível confirmar o arquivo de espera; o limite observado permanece em vigor."
        else:
            result["retry_after_seconds"] = wait
            result["retry_at"] = _services.math.ceil(_services.time.time()) + wait
    phase = getattr(exc, "phase", None)
    if phase in {"browser_setup", "signup_submission", "email_verification", "signup_confirmation"}:
        result["phase"] = phase
    provider_code = getattr(exc, "provider_error_code", None)
    if provider_code in _services.registration_state.PROVIDER_ERROR_CODES:
        result["provider_error_code"] = provider_code
    return result

def _registration_gate(_services, *, claru=False):
    if claru:
        remaining = _services.registration_limits.remaining("minute")
        return ("minute", remaining) if remaining else None
    return _services.registration_limits.check_all()

def _valid_registration_name(_services, value: Any) -> bool:
    return (isinstance(value, str) and len(value) <= 200
            and not any(_services.unicodedata.category(character) in {"Cc", "Cs"}
                        for character in value))

def _hostinger_is_configured(_services) -> bool:
    """Ao menos um perfil Hostinger com rotas (domínio catch-all)?"""
    profiles = getattr(_services.config, "HOSTINGER_MAIL_PROFILES", None) or []
    return any(
        bool(profile.get("routes"))
        for profile in profiles
        if isinstance(profile, dict) and str(profile.get("token") or "").strip()
    )

def _registration_domains(_services) -> list[dict[str, str]]:
    domains = []
    seen: set[str] = set()
    for profile in _services.hostinger_mail.configured_connections():
        for route in profile["routes"]:
            # Uma rota de destinatário individual não configura um catch-all.
            if not route or "@" in route or any(c.isspace() for c in route) or route in seen:
                continue
            seen.add(route)
            domains.append({"domain": route, "profile_id": profile.get("id", ""),
                            "profile_name": profile.get("name", "")})
    return domains

def _recover_hostinger_domains(_services) -> list[str]:
    """Completa perfis antigos sem rotas usando a própria credencial salva."""
    warnings = []
    if not any(not p["routes"] for p in _services.hostinger_mail.configured_connections()):
        return warnings
    # Serializa com salvar/remover integrações para não restaurar dados antigos.
    with _services._INTEGRATION_OPERATION_LOCK:
        secure = _services._migrate_legacy_integrations()
        profiles = _services._hostinger_profiles(secure.get("hostinger") or {})
        discovered = {}
        changed = False
        recovered = []
        for profile in profiles:
            if profile["routes"]:
                recovered.append(profile)
                continue
            token = profile["token"]
            if token not in discovered:
                try:
                    discovered[token] = _services.hostinger_mail.discover_mailboxes(token)
                except Exception as exc:
                    discovered[token] = []
                    warnings.append(_services._integration_error(exc, "Hostinger"))
            matches = [mailbox for mailbox in discovered[token]
                       if not profile["mailbox_id"]
                       or mailbox["resource_id"] == profile["mailbox_id"]]
            usable = [mailbox for mailbox in matches if mailbox.get("domain")]
            if usable:
                for index, mailbox in enumerate(usable):
                    recovered.append({
                        **profile,
                        "id": profile["id"] if index == 0 else _services.uuid.uuid4().hex,
                        "mailbox_id": mailbox["resource_id"],
                        "routes": [mailbox["domain"]],
                        "name": mailbox.get("address") or profile["name"],
                    })
                changed = True
            else:
                recovered.append(profile)
                if not warnings:
                    warnings.append("A Hostinger não informou o domínio da caixa salva. "
                                    "Identifique novamente a API em Integrações.")
        if changed:
            with _services._PERSISTENCE_LOCK:
                secure["hostinger"] = {"profiles": recovered}
                _services.save_secure_settings(_services.config.INTEGRATIONS_PATH, secure)
                _services._apply_hostinger(recovered)
    return warnings

def _preflight_checks(_services, domain: str = "") -> dict[str, Any]:
    """Valida todas as dependências antes de iniciar a criação de contas.

    Retorna um dict com status de cada verificação e um flag 'ready' geral.
    """
    from moneymin import crowtado
    from urllib.error import HTTPError
    checks: dict[str, dict[str, Any]] = {}

    # 1. Hostinger Mail API
    domain = domain.strip().lower().lstrip("@")
    profiles = [p for p in _services.hostinger_mail.configured_connections()
                if domain and domain in p["routes"]]
    if profiles:
        mailboxes = 0
        working_profiles = 0
        for profile in profiles:
            try:
                result = _services.hostinger_mail.test_connection(
                    token=profile.get("token"), mailbox=profile.get("mailbox_id"))
                mailboxes += result["mailboxes"]
                working_profiles += 1
            except Exception:
                continue
        if working_profiles:
            checks["hostinger"] = {"ok": True, "detail": f"{mailboxes} caixa(s) para {domain}"}
        else:
            checks["hostinger"] = {"ok": False, "detail": "Nenhum perfil do domínio respondeu. Confira as credenciais e a conexão."}
    else:
        checks["hostinger"] = {"ok": False, "detail": "Selecione um domínio com rota Hostinger configurada"}

    # 2. Chrome/Playwright
    try:
        from moneymin import crowtado
        chrome_path = crowtado._chrome_exe()
        checks["chrome"] = {"ok": True, "detail": chrome_path}
    except Exception:
        checks["chrome"] = {"ok": False, "detail": "Navegador indisponível. Confira a instalação do QMoney."}

    # 3. Crowtado Clerk API
    try:
        import urllib.request
        from moneymin import tls
        req = urllib.request.Request(
            f"{crowtado.CLERK_BASE}/v1/client",
            method="POST",
        )
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "curl/8.5.0")
        with tls.urlopen(req, timeout=10) as resp:
            if resp.status < 300:
                checks["crowtado_api"] = {"ok": True, "detail": "Clerk API respondendo"}
            else:
                checks["crowtado_api"] = {"ok": False, "detail": f"status {resp.status}"}
    except HTTPError as exc:
        checks["crowtado_api"] = {"ok": False, "http_status": exc.code,
                                  "detail": f"A API Crowtado não respondeu normalmente (HTTP {exc.code}). Tente novamente."}
    except Exception:
        checks["crowtado_api"] = {"ok": False, "detail": "Não foi possível confirmar a API Crowtado. Confira a conexão e tente novamente."}

    # 4. Minute API
    try:
        import urllib.request
        from moneymin import tls
        req = urllib.request.Request(
            f"{_services.config.BASE_URL}/api/v1/health",
            method="GET",
        )
        req.add_header("User-Agent", "okhttp/4.12.0")
        with tls.urlopen(req, timeout=10) as resp:
            checks["minute_api"] = {"ok": True, "detail": f"status {resp.status}"}
    except HTTPError as exc:
        # Only an actual 404 response to this health endpoint has this meaning.
        # Exception prose (including another status with 'Not Found') is not evidence.
        if exc.code == 404:
            checks["minute_api"] = {"ok": True, "http_status": 404,
                                    "detail": "API respondendo (sem /health)"}
        else:
            checks["minute_api"] = {"ok": False, "http_status": exc.code,
                                    "detail": f"A API Minute não respondeu normalmente (HTTP {exc.code}). Tente novamente."}
    except Exception:
        checks["minute_api"] = {"ok": False, "detail": "Não foi possível confirmar a API Minute. Confira a conexão e tente novamente."}

    # 5. Check local configuration without joining or exposing the code.
    checks["invite_code"] = {
        "ok": bool(_services.config.INVITE_CODE),
        "detail": "Código de convite configurado." if _services.config.INVITE_CODE else "Configure o código de convite.",
    }

    ready = all(check.get("ok", False) for check in checks.values())
    return {"ready": ready, "checks": checks}
