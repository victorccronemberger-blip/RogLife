"""Account-domain implementation; invoked with explicit dependency bindings."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('ACCOUNT_HEALTH_PATH', 'AuthError', 'Session', '_PERSISTENCE_LOCK', '_cache_org_key', '_check_crowtado_health', '_check_minute_health', '_identity_route', '_load_account_health_history', '_load_prefs', '_resolve_org', '_save_account_check', '_save_prefs', '_saved_account_password', 'account_health', 'account_issue', 'crowtado', 'decode_json_state', 'issue_text', 'json', 'org_policy', 'save_json', 'sys', 'time', 'token_store')


@contextmanager
def _identity_route(_services, email: str):
    """Use an account-owned route and sanitize failures before they reach UI."""
    from moneymin import registration_proxy
    manager = registration_proxy.account_route(email)
    try:
        manager.__enter__()
    except (OSError, RuntimeError, ValueError):
        raise _services.AuthError("Não foi possível abrir a conexão atribuída a esta conta.",
                        code="network") from None
    try:
        yield
    except BaseException:
        # Preserve the operation's diagnostic (for example `restricted`) if
        # tunnel cleanup also fails while unwinding this identity route.
        try:
            manager.__exit__(*_services.sys.exc_info())
        except BaseException:
            pass
        raise
    else:
        try:
            manager.__exit__(None, None, None)
        except (OSError, RuntimeError, ValueError):
            raise _services.AuthError("Não foi possível encerrar a conexão atribuída a esta conta.",
                            code="network") from None

def _resolve_org(_services, email: str, session: Session | None = None) -> str:
    """Resolve (e cacheia) a org_key pela política da conta, não pela 1ª org."""
    # Validar sempre, inclusive quando a organização já está no cache. O HUB
    # devolve /users/me = 200 para contas desativadas; ensure_auth inspeciona o
    # campo `disabled` e impede que a campanha comece com uma conta bloqueada.
    requested_email = _services.token_store.email_key(email)
    if session is not None and _services.token_store.email_key(getattr(session, "email", None)) != requested_email:
        raise _services.AuthError("A sessão Minute não corresponde à conta solicitada.", code="identity")
    # Resolve membership with the proxy already assigned to this identity.
    # Session.from_email refreshes immediately, so it must be inside the route.
    # ContextVar tokens restore an outer route when this helper is nested.
    with _services._identity_route(email):
        sess = session or _services.Session.from_email(email)
        if _services.token_store.email_key(getattr(sess, "email", None)) != requested_email:
            raise _services.AuthError("A sessão Minute não corresponde à conta solicitada.", code="identity")
        profile = sess.ensure_auth()
        orgs = [org for org in (profile.get("organizations") or []) if isinstance(org, dict)]
        org_key = _services.org_policy.ensure_membership(sess, email, orgs)
        _services._cache_org_key(email, org_key)
        return org_key

def _check_minute_health(_services, email: str) -> dict[str, Any]:
    """Check the target organization and quality state, not just authentication."""
    session = None
    for attempt in range(1, 3):
        try:
            session = session or _services.Session.from_email(email)
            profile = session.ensure_auth()
            if type(profile.get("disabled")) is not bool:
                raise _services.AuthError("Estado da conta Minute incompleto", code="invalid_response")
            organizations = [
                org for org in (profile.get("organizations") or [])
                if isinstance(org, dict)
            ]
            target = next((org for org in organizations if org.get("resourceKey") == _services.org_policy.target_org_key(email)), {})
            if "disabled" in target and type(target["disabled"]) is not bool:
                raise _services.AuthError("Estado da organização Minute incompleto", code="invalid_response")
            try:
                org_key = _services.org_policy.ensure_membership(session, email, organizations)
            except RuntimeError as exc:
                if "suspensa" in str(exc).casefold():
                    raise _services.AuthError(str(exc), code="access_paused") from exc
                raise _services.AuthError(str(exc), code="organization") from exc
            quality = session.checked_quality_state(org_key)
            if not isinstance(quality, dict) or quality.get("userState") not in ("active", "on_hold", "inactive"):
                raise _services.AuthError("Estado de qualidade Minute incompleto", code="invalid_response")
            if quality["userState"] in ("on_hold", "inactive"):
                raise _services.AuthError("A organização alvo pausou o acesso Minute", code="access_paused")
            result = {
                "email": email, "status": "active", "status_label": "Acesso verificado",
                "org_key": org_key, "expires_at": session.data.get("expires_at", 0),
            }
            break
        except Exception as exc:  # noqa: BLE001 — qualquer falha ambígua é inconclusiva
            issue = _services.account_issue(email, exc, stage="Verificação Minute")
            issue["provider"] = "minute"
            if issue["retryable"] and issue["code"] not in {"rate_limit", "account_locked", "access_paused"} and attempt < 2:
                _services.time.sleep(0.5)
                continue
            status, label = {
                "restricted": ("disabled", "Banida · Minute"),
                "authentication": ("needs_reauth", "Reconectar acesso"),
                "missing_access": ("needs_reauth", "Reconectar acesso"),
                "organization": ("needs_org", "Organização pendente"),
            }.get(issue["code"], ("inconclusive", "Verificação inconclusiva"))
            result = {"email": email, "status": status, "status_label": label,
                      "error": _services.issue_text(issue), "issue": issue}
            break
    result["attempts"] = attempt
    result["checked_at"] = _services.time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return result

def _check_crowtado_health(_services, email: str) -> dict[str, Any]:
    if _services.org_policy.account_kind(email) == "claru":
        return {"email": email, "status": "not_applicable", "status_label": "Não se aplica",
                "checked_at": _services.time.strftime("%Y-%m-%dT%H:%M:%S%z"), "attempts": 0}
    for attempt in range(1, 3):
        try:
            password = _services._saved_account_password(email)
            if not password:
                raise _services.crowtado.CrowtadoError("Sem senha Crowtado salva", code="missing_access")
            state = _services.crowtado.verificar_restricoes(email, password)
            if state["restricted"]:
                issue = _services.account_issue(email, _services.crowtado.CrowtadoError("Restrição", code="restricted"),
                                      stage="Verificação Crowtado · saques")
                issue.update(provider="crowtado", reason=state["reason"],
                             action="Confira o painel e solicite regularização ao suporte da Crowtado. A conta permanece disponível para consulta.")
                result = {"email": email, "status": "disabled", "status_label": "Banida · saque suspenso",
                          "restriction_kind": state["restriction_kind"], "access_status": "active",
                          "issue": issue, "error": _services.issue_text(issue)}
            else:
                result = {"email": email, "status": "active", "status_label": "Sem restrição informada",
                          "access_status": "active", "payout_available": state["payout_available"]}
            break
        except Exception as exc:  # noqa: BLE001 — missing evidence never confirms clearance
            issue = _services.account_issue(email, exc, stage="Verificação Crowtado")
            issue["provider"] = "crowtado"
            if issue["retryable"] and issue["code"] not in {"rate_limit", "account_locked", "access_paused"} and attempt < 2:
                _services.time.sleep(0.5)
                continue
            status, label = {"restricted": ("disabled", "Banida · login recusado"),
                             "authentication": ("needs_reauth", "Reconectar Crowtado"),
                             "missing_access": ("needs_reauth", "Conectar Crowtado"),
                             "crowtado_account_missing": ("needs_reauth", "Cadastro não encontrado")}.get(
                                 issue["code"], ("inconclusive", "Verificação inconclusiva"))
            result = {"email": email, "status": status, "status_label": label,
                      "error": _services.issue_text(issue), "issue": issue}
            if status == "disabled":
                result["restriction_kind"] = "account"
                result["access_status"] = "disabled"
            elif getattr(exc, "crowtado_login_verified", False) is True:
                result["access_status"] = "active"
                result["status_label"] = ("Login aceito · perfil pendente"
                                          if issue["code"] == "demographics_required"
                                          else "Login aceito · saque não verificado")
            break
    result.update(attempts=attempt, checked_at=_services.time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    return result

def _check_account_health(_services, email: str) -> dict[str, Any]:
    """Always consult both services independently using the identity's assigned proxy."""
    from moneymin import registration_proxy
    providers = {}
    try:
        with registration_proxy.route(registration_proxy.assign(email, "")):
            providers["minute"] = _services._check_minute_health(email)
            providers["crowtado"] = _services._check_crowtado_health(email)
    except Exception as exc:  # local routing failure must not fall back to another IP
        for name in _services.account_health.SERVICES:
            if name not in providers:
                issue = _services.account_issue(email, exc, stage=f"Verificação {name.title()} · conexão")
                issue["provider"] = name
                providers[name] = {"email": email, "status": "inconclusive", "status_label": "Verificação inconclusiva",
                                   "error": _services.issue_text(issue), "issue": issue, "attempts": 0,
                                   "checked_at": _services.time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    result = _services.account_health.aggregate(email, providers)
    _services._save_account_check(email, result)
    return result

def _save_account_check(_services, email: str, result: dict[str, Any]) -> None:
    with _services._PERSISTENCE_LOCK:
        try:
            health = _services._load_account_health_history()
            previous = health.get(email.strip().casefold(), {})
            candidate = dict(result)
            # Copy nested diagnoses before adding provider-specific history.
            candidate = _services.decode_json_state(_services.json.dumps(candidate))
            _services.account_health.preserve_history(candidate, previous if isinstance(previous, dict) else {})
            candidate = _services.decode_json_state(_services.json.dumps(candidate))
            _services.account_health.validate(candidate, email.strip().casefold())
            health[email.strip().casefold()] = candidate
            _services.save_json(_services.ACCOUNT_HEALTH_PATH, health)
            result["last_success_at"] = candidate["last_success_at"]
            if "providers" in candidate:
                result["providers"] = candidate["providers"]
            # Checking one service must not erase access to the other service.
            minute = _services.account_health.provider(result, "minute")
            if minute.get("status") == "active" and result.get("org_key"):
                prefs = _services._load_prefs()
                prefs.setdefault("org_keys", {})[email] = result["org_key"]
                _services._save_prefs(prefs)
        except (OSError, TypeError, ValueError, RecursionError):
            # Falha no histórico local não altera o diagnóstico remoto.
            result["history_saved"] = False

def _safe_org(_services, email: str) -> str | None:
    try:
        return _services._resolve_org(email)
    except Exception:  # noqa: BLE001 — best-effort
        return None
