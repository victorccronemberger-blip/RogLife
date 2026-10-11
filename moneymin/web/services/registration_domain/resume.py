"""Account-domain implementation; invoked with explicit dependency bindings."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('JsonStateError', 'Session', '_CROWTADO_SIGNUP_RETRIES', '_CROWTADO_SIGNUP_RETRY_DELAY_S', '_STEP_LABELS', '_cache_org_key', '_full_register_account', '_register_account_steps', '_register_claru_steps', '_registration_failure', '_registration_gate', '_save_account_check', '_save_crowtado_cred', '_set_account_removed', '_step_fail', '_step_ok', '_step_skip', '_validate_minute_membership', 'account_bans', 'config', 'credential_store', 'org_policy', 'registration_limits', 'registration_state', 'time', 'token_store')


def _validate_minute_membership(_services, email: str) -> str:
    """Confirma a org obrigatória e migra Crowtado antiga para PE8EAR5V."""
    session = _services.Session.from_email(email)
    profile = session.ensure_auth()
    organizations = [
        org for org in (profile.get("organizations") or [])
        if isinstance(org, dict)
    ]
    try:
        org_key = _services.org_policy.ensure_membership(session, email, organizations)
    except RuntimeError as exc:
        if str(exc) == "Conta suspensa na organização de destino.":
            from moneymin.minute_api import AuthError
            raise AuthError(str(exc), code="access_paused") from None
        raise
    # Membership alone does not exclude account/org suspension or quality hold.
    session.ensure_auth(org_key=org_key)
    _services._cache_org_key(email, org_key)
    return org_key

def _full_register_account(_services, email: str, password: str, identity_data: dict[str, Any],
                           *, on_step: Any = None, resume: bool = False) -> dict[str, Any]:
    blocked = _services._registration_gate(claru=_services.org_policy.account_kind(email) == "claru")
    if blocked:
        provider, remaining = blocked
        previous = _services.registration_state.load().get(email.strip().casefold(), {})
        return {"steps": previous.get("steps", {}), "code": "rate_limit", "not_admitted": True,
                "retry_after_seconds": remaining, "partial": bool(previous),
                "error": f"{provider.title()} limitou o cadastro. Aguarde {remaining} segundo(s)."}
    from moneymin import registration_proxy
    from contextlib import ExitStack
    scope = ExitStack()
    try:
        saved = _services.credential_store.lookup(_services.config.SECRETS_DIR, email, strict=True)
        if saved and saved != password:
            raise ValueError("A conta já possui outra senha protegida. Reconecte o acesso antes de registrar.")
    except ValueError as exc:
        return {"steps": {"save_partial": _services._step_fail(str(exc))}, "error": str(exc), "partial": False}
    identity_data = dict(identity_data)
    previous = _services.registration_state.load().get(email.strip().casefold(), {})
    steps = dict(previous.get("steps", {})) if resume or saved else {}
    try:
        proxy = registration_proxy.assign(email, identity_data.get("proxy_id", ""))
        if proxy:
            identity_data["proxy_id"] = proxy["id"]
        scope.enter_context(registration_proxy.route(proxy))
        if proxy:
            if callable(on_step):
                on_step("proxy")
            ip = registration_proxy.check_exit_ip()
            steps["proxy"] = _services._step_ok(f"{proxy['host']}:{proxy['port']} · IP de saída {ip}")
        else:
            steps["proxy"] = _services._step_skip("Conexão direta selecionada pelo usuário")
    except (OSError, ValueError, RuntimeError):
        scope.close()
        detail = "Não foi possível confirmar o proxy selecionado. Confira a conexão e tente novamente; nenhuma conta foi criada nesta tentativa e nenhuma conexão direta foi usada."
        steps["proxy"] = _services._step_fail(detail)
        if previous.get("state") != "complete":
            _services.registration_state.update(email, identity=identity_data, steps=steps, state="incomplete", error=detail)
        return {"steps": steps, "error": detail,
                "partial": steps.get("save_partial", {}).get("status") == "ok"}
    try:
        _services.registration_state.update(email, identity=identity_data, steps=steps)
        if _services.org_policy.account_kind(email) == "claru":
            return _services._register_claru_steps(email, password, identity_data, on_step=on_step)
        return _services._register_account_steps(email, password, identity_data, on_step=on_step, resume=True)
    finally:
        scope.close()

def _full_register_claru_minute(_services, row, *, on_step=None):
    from moneymin import claru, claru_registration, registration_proxy
    blocked = _services._registration_gate(claru=True)
    if blocked:
        provider, remaining = blocked
        return {"steps": {}, "error": f"Minute limitou o cadastro. Aguarde {remaining} segundo(s).",
                "code": "rate_limit", "not_admitted": True, "retry_after_seconds": remaining,
                "partial": False, "minute_email": row.get("minute_email", "")}
    login_email = row["login_email"]
    minute = row.get("minute_email", "")
    current = "proxy"
    try:
        if callable(on_step):
            on_step(current)
        proxy = registration_proxy.assign(login_email, row["proxy_id"])
        with registration_proxy.route(proxy):
            if proxy:
                registration_proxy.check_exit_ip()
            current = "crowtado_signup"
            if callable(on_step):
                on_step(current)
            connection = claru_registration.connect(row)
            minute = connection["minute_email"]
            identity_data = {"nome": connection["name"], "sobrenome": "", "proxy_id": proxy["id"] if proxy else ""}
            result = _services._full_register_account(minute, row["minute_password"], identity_data, on_step=on_step, resume=True)
            result["minute_email"] = minute
            if result["error"] is None:
                claru_registration.checkpoint(login_email, stage="complete")
            return result
    except Exception as exc:
        detail = str(exc) if isinstance(exc, claru.ClaruError) else "Não foi possível confirmar o cadastro Claru. Preserve o cofre e retome a mesma conta após conferir o acesso."
        step = {"status": "manual" if isinstance(exc, claru.ClaruActionRequired) else "fail", "detail": detail}
        return {"error": detail, "steps": {current: step}, "partial": True,
                "minute_email": minute, "needs_action": isinstance(exc, claru.ClaruActionRequired)}

def _register_claru_steps(_services, email, password, identity_data, *, on_step=None):
    """Register only the verified Claru inbox; ambiguous POSTs only verify access."""
    from moneymin import claru
    from moneymin.minute_api import register_identity as minute_register, login as minute_login
    steps = dict(_services.registration_state.load().get(email, {}).get("steps", {}))
    blocked = _services._registration_gate(claru=True)
    if blocked:
        provider, remaining = blocked
        return {"steps": steps, "error": f"Minute limitou o cadastro. Aguarde {remaining} segundo(s).",
                "code": "rate_limit", "not_admitted": True, "retry_after_seconds": remaining,
                "partial": bool(steps)}
    if any(step.get("retry_at", 0) > _services.time.time() for step in steps.values()):
        return {"steps": steps, "error": "O prazo informado pelo provedor ainda não terminou. Aguarde antes de retomar.",
                "partial": True}

    def record(name, value):
        from moneymin import registration_proxy
        value = dict(value)
        value["detail"] = registration_proxy.redact(value.get("detail", ""), password)
        steps[name] = value
        _services.registration_state.update(email, identity=identity_data, steps=steps)
        if callable(on_step):
            on_step(name)

    def finish(error=None):
        _services.registration_state.update(email, identity=identity_data, steps=steps,
                                  state="incomplete" if error else "complete", error=error)
        return {"steps": steps, "error": error,
                "partial": bool(error and steps.get("save_partial", {}).get("status") == "ok")}

    current = "ban_check"
    try:
        _services.account_bans.require_not_banned(email)
        record(current, _services._step_ok("Conta fora do registro local de banidas."))
        current = "crowtado_signup"
        claru.verify_minute(email)
        record(current, _services._step_skip("Claru conectado e e-mail gerado confirmado; Crowtado não se aplica."))
        current = "save_partial"
        _services._save_crowtado_cred(email, password)
        record(current, _services._step_ok("Senha do Minute protegida separadamente do login Claru."))
        record("demographics", _services._step_skip("Dados do perfil são gerenciados no Claru."))
        record("link_minute", _services._step_skip("O Minute utiliza o endereço da caixa Claru; nenhum vínculo Crowtado se aplica."))
        identity_step = steps.get("minute_identity", {})
        existed = (steps.get("minute_register", {}).get("status") in ("ok", "skip")
                   or identity_step.get("status") in ("ok", "skip")
                   or identity_step.get("remote_effect_possible") is True
                   or _services.token_store.load(_services.config.SECRETS_DIR, email, migrate=False) is not None)
        if not existed:
            current = "minute_identity"
            record(current, {"status": "fail", "detail": "Envio iniciado; aguarda confirmação.",
                             "remote_effect_possible": True})
            try:
                minute_register(email, password, _services.config.CLARU_INVITE_CODE)
            except RuntimeError as exc:
                if not any(marker in str(exc).casefold() for marker in (
                    "email_exists", "email_already_exists", "email already exists",
                    "email already in use", "email-already-in-use")):
                    raise
                existed = True
            else:
                record(current, _services._step_ok("Cadastro Minute aceito; acesso ainda será verificado."))
        current = "minute_register"
        minute_login(email, password)
        if steps.get("minute_identity", {}).get("status") not in ("ok", "skip"):
            record("minute_identity", _services._step_skip("Identidade Minute confirmada pelo acesso."))
        _services._validate_minute_membership(email)
        record(current, _services._step_skip("Minute já existia; acesso e organização Claru confirmados.")
               if existed else _services._step_ok("Minute criado e organização Claru confirmada."))
        current = "validate"
        _services._set_account_removed(email, False)
        record(current, _services._step_ok("Acesso Minute e organização Claru validados."))
        return finish()
    except Exception as exc:
        if isinstance(exc, claru.ClaruError):
            detail = str(exc)
        elif current in ("minute_identity", "minute_register", "validate"):
            detail = "Não foi possível confirmar o cadastro Minute e a organização Claru. Retome a mesma conta para verificar o acesso; nenhum novo cadastro será repetido automaticamente."
        else:
            detail = "Não foi possível confirmar o acesso Claru ou proteger as credenciais locais. Nenhum novo cadastro Minute foi iniciado."
        failed = (_services._registration_failure(exc, provider="minute", detail=detail)
                  if current in ("minute_identity", "minute_register", "validate") else _services._step_fail(detail))
        if current == "minute_identity" and failed.get("remote_effect_possible") is not False:
            failed["remote_effect_possible"] = True
        record(current, failed)
        return finish(detail)

def _register_account_steps(_services, 
    email: str, password: str, identity_data: dict[str, Any],
    *, on_step: Any = None, resume: bool = False,
) -> dict[str, Any]:
    """Conclui cadastro, perfil e tarefa reutilizando a sessão Crowtado."""
    import logging
    import time

    from moneymin import crowtado, account_bans as bans, registration_proxy
    from moneymin.minute_api import register_identity as minute_register, login as minute_login

    logger = logging.getLogger("moneymin.register")
    # Do not overwrite the checkpoint or secret of an already connected account
    # when a new attempt supplies a different password.
    try:
        saved_password = _services.credential_store.lookup(_services.config.SECRETS_DIR, email, strict=True)
        if saved_password and saved_password != password:
            raise ValueError("A conta já possui outra senha protegida. Reconecte o acesso com a senha correta antes de registrar.")
    except ValueError as exc:
        return {"steps": {"save_partial": _services._step_fail(str(exc))}, "error": str(exc), "partial": False}
    previous = _services.registration_state.load().get(email.strip().casefold(), {}) if resume else {}
    steps: dict[str, dict[str, str]] = dict(previous.get("steps", {}))
    step_start: dict[str, float] = {}

    blocked = _services.registration_limits.check_all()
    if blocked:
        provider, remaining = blocked
        return {"steps": steps, "error": f"Aguarde {remaining} segundo(s) antes de acessar {provider.title()} novamente; o provedor limitou o cadastro.", "partial": bool(previous)}

    for step in steps.values():
        if step.get("retry_at", 0) > time.time():
            return {"steps": steps, "error": "O prazo informado pelo provedor ainda não terminou. Aguarde antes de retomar a mesma conta.", "partial": True}

    def confirmed(name):
        return resume and steps.get(name, {}).get("status") in ("ok", "skip")

    def failure(exc):
        return _services._registration_failure(exc)

    if not (confirmed("demographics") and confirmed("link_minute")):
        steps.pop("validate", None)

    def finish(result):
        # Public diagnostics never contain the supplied secret.
        result = dict(result)
        if isinstance(result.get("error"), str):
            result["error"] = registration_proxy.redact(result["error"], password)
        _services.registration_state.update(email, identity=identity_data, steps=steps,
                                  state="incomplete" if result.get("error") else "complete",
                                  error=result.get("error"))
        result.setdefault("partial", bool(result.get("error") and steps.get("save_partial", {}).get("status") == "ok"))
        return result

    def _notify(step_name: str) -> None:
        _services.registration_state.update(email, identity=identity_data, steps=steps)
        step_start[step_name] = time.monotonic()
        logger.info("[register] %s — iniciando etapa: %s", email, _services._STEP_LABELS.get(step_name, step_name))
        if callable(on_step):
            try:
                on_step(step_name)
            except (OSError, _services.JsonStateError):
                raise
            except Exception:
                pass

    def _record_step(step_name: str, result: dict[str, str]) -> None:
        result = dict(result)
        result["detail"] = registration_proxy.redact(result.get("detail", ""), password)
        steps[step_name] = result
        _services.registration_state.update(email, identity=identity_data, steps=steps)
        elapsed = time.monotonic() - step_start.get(step_name, time.monotonic())
        logger.info("[register] %s — %s: %s (%.1fs)",
                     email, step_name, result["status"], elapsed)

    def complete_registration(crowtado_session):
        _record_step("ban_check", _services._step_ok("Login Crowtado aceito; nenhum bloqueio de autenticação informado."))

        if not confirmed("demographics"):
            _notify("demographics")
            _record_step("demographics", {"status": "fail", "detail": "Aguardando confirmação do perfil Crowtado.", "remote_effect_possible": True})
            try:
                crowtado.preencher_demografia(email, password, month, year, gender, session=crowtado_session)
            except (OSError, _services.JsonStateError):
                raise
            except Exception as exc:
                result = failure(exc)
                result.setdefault("remote_effect_possible", True)
                _record_step("demographics", result)
                return finish({"steps": steps, "error": f"perfil Crowtado: {exc}"})
            _record_step("demographics", _services._step_ok("Dados do perfil confirmados pela Crowtado."))

        # Step 4: Minute register (com invite code da org)
        _notify("minute_register")
        try:
            existed = False
            try:
                # O código é explícito para que nenhuma alteração de default consiga
                # cadastrar uma conta Crowtado em organização diferente.
                if confirmed("minute_register") or confirmed("minute_identity") or steps.get("minute_identity", {}).get("remote_effect_possible") is True:
                    minute_login(email, password)
                    existed = True
                else:
                    _record_step("minute_identity", {"status": "fail", "detail": "Envio iniciado; aguarda confirmação.", "remote_effect_possible": True})
                    try:
                        minute_register(email, password, _services.config.INVITE_CODE)
                    except Exception as exc:
                        result = failure(exc)
                        if result.get("remote_effect_possible") is not False:
                            result["remote_effect_possible"] = True
                        _record_step("minute_identity", result)
                        raise
                    _record_step("minute_identity", _services._step_ok("Cadastro Minute aceito; acesso ainda será verificado."))
                    minute_login(email, password)
            except RuntimeError as exc:
                # Apenas uma resposta explícita de e-mail duplicado permite recuperação.
                detail = str(exc).casefold()
                if not any(marker in detail for marker in (
                    "email_exists", "email_already_exists", "email already exists",
                    "email already in use", "email-already-in-use",
                )):
                    raise
                minute_login(email, password)
                existed = True
            if not confirmed("minute_identity"):
                _record_step("minute_identity", _services._step_skip("identidade confirmada pelo acesso"))
            _services._validate_minute_membership(email)
        except Exception as exc:
            _record_step("minute_register", failure(exc))
            return finish({"steps": steps, "error": f"minute: {exc}"})
        _record_step("minute_register", _services._step_skip("já existia; acesso e organização confirmados")
                     if existed else _services._step_ok("registro e organização confirmados"))

        if not confirmed("link_minute"):
            _notify("link_minute")
            _record_step("link_minute", {"status": "fail", "detail": "Aguardando confirmação do vínculo Minute na tarefa.", "remote_effect_possible": True})
            try:
                crowtado.vincular_minute(email, password, session=crowtado_session)
            except (OSError, _services.JsonStateError):
                raise
            except Exception as exc:
                result = failure(exc)
                result.setdefault("remote_effect_possible", True)
                _record_step("link_minute", result)
                return finish({"steps": steps, "error": f"vínculo Minute: {exc}"})
            _record_step("link_minute", _services._step_ok("E-mail Minute vinculado e confirmado na tarefa Crowtado."))

        _notify("validate")
        try:
            # O tombstone só é removido quando todo o fluxo remoto foi confirmado.
            # Assim uma tentativa falha nunca ressuscita uma conta excluida.
            _services._set_account_removed(email, False)
        except (OSError, ValueError):
            detail = "conta criada e validada, mas a ativação local não pôde ser salva"
            _record_step("validate", _services._step_fail(detail))
            return finish({"steps": steps, "error": f"validação: {detail}", "partial": True})
        _record_step("validate", _services._step_ok("Crowtado, perfil e vínculo Minute confirmados."))
        _services._save_account_check(email, {"email": email, "status": "active", "status_label": "Acesso verificado",
                                   "org_key": _services.config.ORG_KEY, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
        logger.info("[register] %s — fluxo completo com sucesso", email)

        return finish({"steps": steps, "error": None})

    # Step 0: ban check
    _notify("ban_check")
    try:
        bans.require_not_banned(email)
        _record_step("ban_check", _services._step_ok())
    except ValueError as exc:
        _record_step("ban_check", _services._step_fail(str(exc)))
        return finish({"steps": steps, "error": f"ban: {exc}"})

    # O checkpoint local vem antes de qualquer operação remota. Sem uma senha
    # exportável e relida, nenhuma conta nova será iniciada.
    _notify("save_partial")
    try:
        _services._save_crowtado_cred(email, password)
    except Exception:
        detail = "Não foi possível confirmar o acesso no armazenamento local. Nenhuma criação remota foi iniciada; confira o armazenamento antes de tentar novamente."
        _record_step("save_partial", _services._step_fail(detail))
        return finish({"steps": steps, "error": detail, "partial": False})
    _record_step("save_partial", _services._step_ok("credenciais salvas"))

    # Validate the required profile before any signup or password login.
    if not confirmed("demographics"):
        month, year, gender = (identity_data.get(key) for key in ("birth_month", "birth_year", "gender"))
        if (type(month) is not int or not 1 <= month <= 12
                or type(year) is not int or not 1900 <= year <= time.localtime().tm_year
                or (year, month) > (time.localtime().tm_year, time.localtime().tm_mon)
                or gender not in crowtado.GENDERS):
            detail = "Informe mês e ano de nascimento e gênero reais do titular para concluir o perfil e vincular o Minute."
            _record_step("demographics", {"status": "manual", "detail": detail, "code": "demographics_required"})
            return finish({"steps": steps, "error": detail})

    # Step 1: Crowtado signup (Chrome + Turnstile + email OTP) — com retry
    _notify("crowtado_signup")
    crowtado_signup_ok = confirmed("crowtado_signup")
    crowtado_session = None
    onboarding_result = {}
    if not crowtado_signup_ok and steps.get("crowtado_signup", {}).get("remote_effect_possible") is True:
        # A crash or uncertain response after submit must never create again.
        try:
            crowtado_session = crowtado.login(email, password)
        except Exception as exc:
            result = failure(exc)
            result["remote_effect_possible"] = True
            _record_step("crowtado_signup", result)
            return finish({"steps": steps, "error": "O cadastro anterior continua sem confirmação. Confira a mesma conta no site; nenhum novo cadastro foi enviado."})
        _record_step("crowtado_signup", _services._step_skip("cadastro anterior confirmado pelo login"))
        crowtado_signup_ok = True
    last_error: str = ""
    last_failure = None
    for attempt in range(1, _services._CROWTADO_SIGNUP_RETRIES + 1):
        if crowtado_signup_ok:
            break
        _record_step("crowtado_signup", {"status": "fail", "detail": "Envio iniciado; aguarda confirmação.", "remote_effect_possible": True})
        progress_state = {"event": None}

        def signup_progress(event):
            details = {
                "captcha": "Aguardando CAPTCHA: conclua a verificação no navegador aberto (até cinco minutos).",
                "submitting": "Enviando o formulário Crowtado uma única vez.",
                "confirmation": "Aguardando a confirmação do cadastro Crowtado.",
                "email": "Aguardando o código de verificação por e-mail.",
            }
            if event not in details:
                return
            if callable(on_step):
                # Also called during waits, so a stop request is observed even
                # when the visible stage has not changed.
                on_step("crowtado_" + event)
            if event != progress_state["event"]:
                _record_step("crowtado_signup", {
                    "status": "manual" if event == "captcha" else "fail",
                    "detail": details[event], "remote_effect_possible": True,
                })
                progress_state["event"] = event

        def signup_authenticated(session):
            _record_step("crowtado_signup", _services._step_ok("Cadastro e sessão confirmados no navegador."))
            onboarding_result["result"] = complete_registration(session)

        try:
            progress_options = {"on_authenticated": signup_authenticated}
            if callable(on_step):
                progress_options["on_progress"] = signup_progress
            if identity_data.get("use_referral") is False:
                crowtado.criar_conta(email, password, ref="", **progress_options)
            else:
                crowtado.criar_conta(email, password, **progress_options)
        except (OSError, _services.JsonStateError):
            raise
        except Exception as exc:
            if getattr(exc, "account_issue_code", None) in {
                    "restricted", "account_locked", "rate_limit", "device", "captcha_required",
                    "registration_cancelled", "signup_browser_closed", "signup_browser_unavailable",
                    "signup_not_ready", "signup_field_unconfirmed", "signup_form_invalid"}:
                failed = failure(exc)
                if failed.get("remote_effect_possible") is not False:
                    failed["remote_effect_possible"] = True
                _record_step("crowtado_signup", failed)
                return finish({"steps": steps, "error": f"crowtado: {exc}"})
            error_msg = str(exc)
            lower = error_msg.lower()
            # Somente duplicidade explícita permite retomar uma conta. Erros
            # como "executable doesn't exist" não indicam cadastro existente.
            if any(marker in lower for marker in (
                "form_identifier_exists", "email_exists", "account already exists",
                "email already exists", "email address already exists",
                "email is already taken", "email address is already taken",
                "email address is taken", "email already in use",
                "email address is already in use", "conta já existe",
                "e-mail já existe", "email já existe", "e-mail já está em uso",
                "email já está em uso", "endereço de e-mail já está em uso",
            )):
                try:
                    crowtado_session = crowtado.login(email, password)
                except Exception as login_exc:
                    duplicate_failure = failure(login_exc)
                    duplicate_failure["remote_effect_possible"] = True
                    duplicate_failure["provider_error_code"] = "form_identifier_exists"
                    _record_step("crowtado_signup", duplicate_failure)
                    return finish({"steps": steps, "error": f"crowtado: {login_exc}"})
                # A local checkpoint error is not a remote login error.
                _record_step("crowtado_signup", _services._step_skip("conta já existia; login OK"))
                crowtado_signup_ok = True
                break
            last_error = error_msg
            if getattr(exc, "remote_effect_possible", None) is not False:
                result = failure(exc)
                result["remote_effect_possible"] = True
                _record_step("crowtado_signup", result)
                return finish({"steps": steps, "error": "O envio Crowtado ficou sem confirmação. Retome a mesma conta para verificar o acesso; não será repetida a criação."})
            last_failure = failure(exc)
            _record_step("crowtado_signup", last_failure)
            if attempt < _services._CROWTADO_SIGNUP_RETRIES:
                logger.info("[register] %s — Crowtado falhou (tentativa %d/%d)",
                             email, attempt, _services._CROWTADO_SIGNUP_RETRIES)
                time.sleep(_services._CROWTADO_SIGNUP_RETRY_DELAY_S)
        else:
            if "result" in onboarding_result:
                # The continuation already saved the terminal state while
                # signup's browser was open. Do not reset it to running.
                return onboarding_result["result"]
            # The remote effect succeeded. Stop if its checkpoint cannot be
            # saved; never interpret disk failure as permission to create again.
            _record_step("crowtado_signup", _services._step_ok(
                "conta criada" if attempt == 1 else f"conta criada (tentativa {attempt})"
            ))
            crowtado_signup_ok = True
            break
    if not crowtado_signup_ok:
        final_failure = dict(last_failure or _services._step_fail(last_error))
        final_failure["detail"] = f"{last_error} (após {_services._CROWTADO_SIGNUP_RETRIES} tentativas)"
        _record_step("crowtado_signup", final_failure)
        return finish({"steps": steps, "error": f"crowtado: {last_error}"})

    if crowtado_session is None:
        try:
            crowtado_session = crowtado.login(email, password)
        except Exception as exc:
            _record_step("ban_check", failure(exc))
            return finish({"steps": steps, "error": f"verificação Crowtado: {exc}"})
    return complete_registration(crowtado_session)
