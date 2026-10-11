"""Route handlers grouped by account workflow."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('BALANCES_RUNNER', 'JsonStateError', 'RECOVERY', 'RUNNER', '_BULK_REGISTER_LOCK', '_BULK_REGISTER_STATE', '_STEP_LABELS', '_full_register_account', '_full_register_claru_minute', '_hostinger_is_configured', '_list_accounts', '_registration_domains', '_registration_gate', '_removed_accounts', '_save_registration_batch', '_saved_account_password', '_valid_registration_name', 'account_health', 'bulk_register_accounts', 'config', 'credential_store', 'crowtado', 'datetime', 'hashlib', 'identity', 'json', 'jsonify', 'org_policy', 'registration_state', 'request', 'threading')


def bulk_register_status(_services):
    with _services._BULK_REGISTER_LOCK:
        response = _services.jsonify(_services.json.loads(_services.json.dumps(_services._BULK_REGISTER_STATE)))
        response.headers["Cache-Control"] = "no-store"
        return response

def stop_bulk_register(_services):
    with _services._BULK_REGISTER_LOCK:
        if _services._BULK_REGISTER_STATE.get("state") == "running":
            _services._BULK_REGISTER_STATE["state"] = "stopping"
            _services._save_registration_batch()
        return _services.jsonify({"ok": True, "state": _services._BULK_REGISTER_STATE.get("state", "idle")})

def resume_account(_services, email: str):
    try:
        key = _services.credential_store.email_key(email)
        row = _services.registration_state.load().get(key)
        password = _services._saved_account_password(key)
    except ValueError:
        return _services.jsonify({"error": "Conta inválida."}), 400
    if not row or not password or key in _services._removed_accounts():
        return _services.jsonify({"error": "Cadastro ou credencial de retomada indisponível."}), 404
    pending_profile = (_services.org_policy.account_kind(key) != "claru" and any(
        row["steps"].get(name, {}).get("status") not in {"ok", "skip"}
        for name in ("demographics", "link_minute")))
    if row["state"] == "complete" and not pending_profile:
        return _services.jsonify({"error": "Esta conta já possui cadastro completo."}), 409
    request_body = _services.request.get_json(silent=True) or {}
    return _services.bulk_register_accounts(single={"email": key, "password": password,
                                         **row["identity"], "resume": True,
                                         **{field: request_body[field] for field in ("birth_month", "birth_year", "gender") if field in request_body},
                                         "request_id": request_body.get("request_id", "")})

def bulk_register_accounts(_services, single=None):
    """Cria contas Crowtado + Minute com proxy selecionado e progresso durável."""
    with _services._BULK_REGISTER_LOCK:
        if _services._BULK_REGISTER_STATE.get("state") in ("running", "stopping"):
            return _services.jsonify({"error": "Já existe uma criação em andamento."}), 409

    body = single or _services.request.get_json(silent=True) or {}
    provider = body.get("provider", "crowtado")
    if provider not in ("crowtado", "claru"):
        return _services.jsonify({"error": "Provedor de cadastro inválido."}), 400
    claru_batch = provider == "claru"
    from moneymin import registration_proxy
    proxy_id = body.get("proxy_id", "")
    try:
        registration_proxy.validate_selection(proxy_id)
    except (OSError, ValueError):
        return _services.jsonify({"error": "Selecione um proxy disponível ou importe o arquivo TXT."}), 400
    use_referral = body.get("use_referral", True)
    if type(use_referral) is not bool:
        return _services.jsonify({"error": "use_referral deve ser booleano."}), 400
    request_body = _services.request.get_json(silent=True) or {}
    request_id = request_body.get("request_id", "")
    if not isinstance(request_id, str) or len(request_id) > 80:
        return _services.jsonify({"error": "Identificador de cadastro inválido."}), 400
    request_fingerprint = _services.hashlib.sha256((_services.request.path + _services.json.dumps(request_body, sort_keys=True)).encode()).hexdigest()
    manual = "email" in body and not claru_batch
    if manual:
        try:
            email = _services.credential_store.email_key(body.get("email"))
            password = body.get("password")
            if not isinstance(password, str) or not password or len(password) > 4096:
                raise ValueError("Informe uma senha válida.")
            month, year = body.get("birth_month"), body.get("birth_year")
            if (("birth_month" in body and (type(month) is not int or not 1 <= month <= 12))
                    or ("birth_year" in body and (type(year) is not int or not 1900 <= year <= _services.datetime.date.today().year))
                    or ("gender" in body and body.get("gender") not in _services.crowtado.GENDERS)):
                raise ValueError("Informe mês, ano de nascimento e gênero válidos para o cadastro.")
            identity_data = {"email": email, "senha": password,
                             "nome": body.get("nome", email.split("@")[0]),
                             "sobrenome": body.get("sobrenome", ""),
                              **{key: body[key] for key in ("birth_month", "birth_year", "gender") if key in body}}
            if (not _services._valid_registration_name(identity_data["nome"])
                    or not _services._valid_registration_name(identity_data["sobrenome"])):
                return _services.jsonify({"error": "Nome e sobrenome devem ser textos de até 200 caracteres, sem controles.",
                                "code": "invalid_identity"}), 400
        except ValueError as exc:
            return _services.jsonify({"error": str(exc)}), 400
    try:
        count = len(body["accounts"]) if claru_batch and isinstance(body.get("accounts"), list) else (1 if manual else body.get("count", 0))
        if type(count) is not int:
            raise ValueError
    except (TypeError, ValueError):
        return _services.jsonify({"error": "count inválido"}), 400
    domain = "supply.claru.ai" if claru_batch else (email.split("@", 1)[1] if manual else str(body.get("domain", "")).strip().lower().lstrip("@"))
    if count < 1 or count > 50:
        return _services.jsonify({"error": "count deve estar entre 1 e 50"}), 400
    profiles = body.get("profiles")
    if profiles is not None and not manual and not claru_batch:
        today = _services.datetime.date.today()
        if (not isinstance(profiles, list) or len(profiles) != count or any(
                not isinstance(profile, dict)
                or type(profile.get("birth_month")) is not int or not 1 <= profile["birth_month"] <= 12
                or type(profile.get("birth_year")) is not int or not 1900 <= profile["birth_year"] <= today.year
                or (profile["birth_year"], profile["birth_month"]) > (today.year, today.month)
                or profile.get("gender") not in _services.crowtado.GENDERS
                for profile in profiles)):
            return _services.jsonify({"error": "Informe os dados reais de perfil para cada titular do lote."}), 400
    if not domain:
        return _services.jsonify({"error": "domain é obrigatório"}), 400
    if _services.RUNNER.running or _services.RECOVERY.running or _services.BALANCES_RUNNER.running:
        return _services.jsonify({"error": "pare a campanha antes de criar contas"}), 409
    is_claru = manual and _services.org_policy.account_kind(email) == "claru"
    if is_claru:
        from moneymin import claru
        try:
            claru.account_for_minute(email)
        except (ValueError, RuntimeError):
            return _services.jsonify({"error": "Conecte primeiro a conta Claru para confirmar o endereço gerado do Minute."}), 409
    if not is_claru and not claru_batch and not _services._hostinger_is_configured():
        return _services.jsonify({"error": "Configure a integração Hostinger (domínio catch-all) antes de criar contas."}), 409

    if not is_claru and not claru_batch and domain not in {row["domain"] for row in _services._registration_domains()}:
        return _services.jsonify({"error": "Selecione um domínio catch-all configurado nas integrações."}), 400

    blocked = _services._registration_gate(claru=claru_batch or is_claru)
    if blocked:
        provider, remaining = blocked
        return _services.jsonify({"error": f"{provider.title()} limitou o cadastro. Aguarde {remaining} segundo(s) antes de iniciar ou retomar.",
                        "code": "rate_limit", "retry_after_seconds": remaining,
                        "not_admitted": True}), 429

    existing_emails = {
        account["email"].lower()
        for account in _services._list_accounts()
    }

    with _services._BULK_REGISTER_LOCK:
        # Recheck admission after identity/domain/storage reads. In normal
        # HTTP requests _ACCOUNT_OPERATION_LOCK serializes account writes;
        # this second check also protects internal callers and state
        # transitions made by other local workers during that window.
        if _services._BULK_REGISTER_STATE.get("state") in ("running", "stopping"):
            return _services.jsonify({"error": "Já existe uma criação em andamento."}), 409
        if _services.RUNNER.running or _services.RECOVERY.running or _services.BALANCES_RUNNER.running:
            return _services.jsonify({"error": "pare a campanha antes de criar contas"}), 409
        if claru_batch:
            from moneymin import claru_registration
            try:
                claru_rows = claru_registration.prepare(body.get("accounts"), proxy_id)
            except ValueError as exc:
                return _services.jsonify({"error": str(exc)}), 400
            except (OSError, RuntimeError):
                return _services.jsonify({"error": "Não foi possível proteger o lote Claru. Nenhuma conta foi enviada."}), 503
        _services._BULK_REGISTER_STATE.clear()
        _services._BULK_REGISTER_STATE.update({
            "state": "running",
            "total": count,
            "completed": 0,
            "created": 0,
            "failed": 0,
            "results": [],
            "current_email": "",
            "current_step": "",
            "domain": domain,
            "provider": "claru" if claru_batch or is_claru else "crowtado",
            "stopped": False,
            "request_id": request_id,
            "request_fingerprint": request_fingerprint,
        })
        try:
            _services._save_registration_batch()
        except (OSError, _services.JsonStateError):
            # No remote work has started. Do not leave the UI locked in a
            # running state when the initial durable checkpoint failed.
            _services._BULK_REGISTER_STATE.update(
                state="failed", request_id="", request_fingerprint="",
                error="Não foi possível salvar o progresso local. Nenhuma conta foi criada."
            )
            return _services.jsonify({"error": _services._BULK_REGISTER_STATE["error"],
                            "not_admitted": True}), 503

    def _run_batch() -> bool:
        successes = 0
        failures = 0
        results: list[dict[str, Any]] = []
        used_emails: set[str] = set(existing_emails)
        for index in range(count):
            with _services._BULK_REGISTER_LOCK:
                if _services._BULK_REGISTER_STATE.get("state") == "stopping":
                    _services._BULK_REGISTER_STATE["stopped"] = True
                    break
            try:
                account_identity = ({"email": claru_rows[index]["login_email"], "senha": claru_rows[index]["minute_password"],
                                     "nome": "", "sobrenome": ""} if claru_batch else dict(identity_data) if manual else _services.identity.gerar_identidade(
                    domain=domain,
                    existentes=used_emails,
                ))
            except RuntimeError as exc:
                with _services._BULK_REGISTER_LOCK:
                    _services._BULK_REGISTER_STATE["state"] = "failed"
                    _services._BULK_REGISTER_STATE["error"] = str(exc)
                return False
            email = account_identity["email"]
            password = account_identity["senha"]
            if not manual:
                for key in ("gender", "birth_month", "birth_year"):
                    account_identity.pop(key, None)
                if not claru_batch and profiles is not None:
                    account_identity.update({key: profiles[index][key] for key in ("gender", "birth_month", "birth_year")})
            account_identity["use_referral"] = use_referral
            account_identity["proxy_id"] = proxy_id
            used_emails.add(email.lower())

            def _on_step(step_name: str) -> None:
                with _services._BULK_REGISTER_LOCK:
                    signup_labels = {
                        "crowtado_captcha": "Aguardando CAPTCHA: conclua a verificação no navegador aberto (até cinco minutos).",
                        "crowtado_submitting": "Enviando o formulário Crowtado uma única vez.",
                        "crowtado_confirmation": "Aguardando a confirmação do cadastro Crowtado.",
                        "crowtado_email": "Aguardando o código de verificação por e-mail.",
                    }
                    if step_name in signup_labels and _services._BULK_REGISTER_STATE.get("state") == "stopping":
                        _services._BULK_REGISTER_STATE["stopped"] = True
                        raise _services.crowtado.CrowtadoError(
                            "Cadastro interrompido pelo usuário. O progresso foi preservado; confira e retome a mesma conta.",
                            code="registration_cancelled")
                    if (step_name in signup_labels
                            and _services._BULK_REGISTER_STATE.get("current_email") == email
                            and _services._BULK_REGISTER_STATE.get("current_step") == signup_labels[step_name]):
                        return
                    _services._BULK_REGISTER_STATE["current_email"] = email
                    claru_labels = {"ban_check": "verificação de acesso", "crowtado_signup": "confirmar perfil Claru",
                                    "demographics": "perfil Claru", "link_minute": "identidade Claru → Minute"}
                    _services._BULK_REGISTER_STATE["current_step"] = (claru_labels.get(step_name, _services._STEP_LABELS.get(step_name, step_name))
                                                           if is_claru or claru_batch else signup_labels.get(step_name, _services._STEP_LABELS.get(step_name, step_name)))
                    _services._save_registration_batch()

            if claru_batch:
                result = _services._full_register_claru_minute(claru_rows[index], on_step=_on_step)
                email = result.get("minute_email") or email
            else:
                result = _services._full_register_account(email, password, account_identity, on_step=_on_step,
                                                **({"resume": True} if body.get("resume") is True else {}))
            ok = result["error"] is None
            removable = (
                _services.config.token_path(email).exists()
                or _services.credential_store.record_path(_services.config.SECRETS_DIR, email).exists()
            )
            if ok:
                successes += 1
            else:
                failures += 1
            results.append({
                "email": email,
                **({"claru_login": claru_rows[index]["login_email"], "minute_email": result.get("minute_email", ""),
                    "needs_action": result.get("needs_action", False)} if claru_batch else {}),
                "nome": account_identity["nome"],
                "sobrenome": account_identity["sobrenome"],
                "gender": account_identity.get("gender"),
                "birth_month": account_identity.get("birth_month"),
                "birth_year": account_identity.get("birth_year"),
                "created": ok,
                "partial": result.get("partial", False),
                "removable": removable,
                "removed": False,
                "error": result["error"],
                "steps": result["steps"],
            })
            with _services._BULK_REGISTER_LOCK:
                _services._BULK_REGISTER_STATE["completed"] = index + 1
                _services._BULK_REGISTER_STATE["created"] = successes
                _services._BULK_REGISTER_STATE["failed"] = failures
                _services._BULK_REGISTER_STATE["results"] = list(results)
                _services._save_registration_batch()
            if _services.account_health.confirmed_ban(email, {}, {"steps": result["steps"]}):
                # A confirmed access restriction stops this batch before
                # another identity can be generated or submitted remotely.
                with _services._BULK_REGISTER_LOCK:
                    _services._BULK_REGISTER_STATE["stopped"] = True
                    _services._BULK_REGISTER_STATE["stop_reason"] = (
                        "Lote interrompido: restrição de acesso confirmada após o cadastro. "
                        "Confira Banidas e solicite revisão ao suporte da plataforma.")
                    _services._save_registration_batch()
                break
            if not ok and any(
                    step.get("code") in {"rate_limit", "account_locked", "restricted", "device", "captcha_required",
                                         "registration_cancelled", "signup_browser_closed", "signup_browser_unavailable",
                                         "signup_not_ready", "signup_field_unconfirmed", "signup_form_invalid", "demographics_required",
                                         "minute_task_unavailable"}
                    or step.get("remote_effect_possible") is True
                    for step in result["steps"].values()):
                with _services._BULK_REGISTER_LOCK:
                    _services._BULK_REGISTER_STATE.update(state="failed", error="Lote interrompido para verificar a conta atual e respeitar a resposta do provedor. Nenhuma próxima conta foi iniciada. " + (result["error"] or ""))
                return False
        return True
    def _worker() -> None:
        terminal = {"state": "failed", "error": "Cadastro interrompido."}
        try:
            if _run_batch():
                terminal = {"state": "done"}
            else:
                terminal = {"state": "failed", "error": _services._BULK_REGISTER_STATE.get("error", "Cadastro interrompido.")}
        except (OSError, _services.JsonStateError):
            terminal = {
                "state": "failed",
                "error": "O armazenamento local não confirmou o progresso do cadastro. As credenciais e etapas já salvas foram preservadas; confira a conta incompleta e retome o mesmo e-mail.",
            }
        except Exception:
            terminal = {
                "state": "failed",
                "error": "Não foi possível concluir o cadastro. Confira as contas e credenciais salvas antes de tentar novamente.",
            }
        finally:
            with _services._BULK_REGISTER_LOCK:
                _services._BULK_REGISTER_STATE.update(terminal)
                _services._BULK_REGISTER_STATE["current_email"] = ""
                _services._BULK_REGISTER_STATE["current_step"] = ""
                try:
                    _services._save_registration_batch()
                except (OSError, _services.JsonStateError):
                    _services._BULK_REGISTER_STATE.update(state="failed", error="Não foi possível salvar o progresso local. Confira as credenciais e os cadastros antes de tentar novamente.")

    try:
        thread = _services.threading.Thread(target=_worker, name="moneymin-bulk-register", daemon=True)
        thread.start()
    except Exception:
        with _services._BULK_REGISTER_LOCK:
            # Thread.start failed before the worker could perform any
            # remote effect. A same-ID retry is therefore safe; persist
            # that this request was not admitted if storage is available.
            _services._BULK_REGISTER_STATE.update(
                state="failed", request_id="", request_fingerprint="",
                error="Não foi possível iniciar o cadastro. Nenhuma conta foi criada."
            )
            try:
                _services._save_registration_batch()
            except (OSError, _services.JsonStateError):
                # The previous durable running checkpoint remains
                # intentionally conservative across a service restart.
                pass
        return _services.jsonify({"error": "Não foi possível iniciar o cadastro.",
                        "not_admitted": True}), 500
    return _services.jsonify({"ok": True, "total": count, "domain": domain, "request_id": request_id})
