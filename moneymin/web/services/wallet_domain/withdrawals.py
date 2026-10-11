"""Account-domain implementation; invoked with explicit dependency bindings."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('JsonStateError', '_PAYOUT_OPERATION_LOCK', '_PERSISTENCE_LOCK', '_WITHDRAW_BULK_LOADED', '_WITHDRAW_BULK_LOCK', '_WITHDRAW_BULK_STATE', '_WITHDRAW_COOLDOWN_LOADED', '_WITHDRAW_COOLDOWN_S', '_WITHDRAW_IN_FLIGHT', '_WITHDRAW_LAST_REQUEST', '_WITHDRAW_LOCK', '_confirmed_available_balance', '_finish_wise_cleanup', '_finite_state_number', '_invalid_local_state', '_invalidate_balance_after_withdrawal', '_load_balances', '_load_withdraw_bulk_locked', '_load_withdraw_cooldowns_locked', '_save_withdraw_bulk_locked', '_wise_cleanup_path', '_wise_cleanup_snapshot', '_withdraw_bulk_path', '_withdraw_bulk_run', '_withdraw_cooldown_path', '_withdraw_message', '_withdraw_once', '_withdraw_once_locked', '_withdraw_paypal_flow', '_withdraw_wise_flow', 'account_health', 'account_issue', 'banned_store', 'config', 'crowtado', 'datetime', 'json', 'load_json_state', 'math', 'org_policy', 're', 'save_json', 'threading', 'time')


def _withdraw_bulk_path(_services) -> Path:
    return _services.config.DATA_DIR / "withdraw_bulk_state.json"

def _withdraw_cooldown_path(_services) -> Path:
    return _services.config.DATA_DIR / "withdraw_request_times.json"

def _wise_cleanup_path(_services) -> Path:
    return _services.config.DATA_DIR / "wise_cleanup_pending.json"

def _wise_cleanup_snapshot(_services) -> dict[str, Any]:
    # Corrupção não pode ser interpretada como ausência de pendência.
    try:
        state = _services.json.loads(_services._wise_cleanup_path().read_text(encoding="utf-8"))
        if not isinstance(state, dict) or not isinstance(state.get("pending"), bool):
            raise ValueError("invalid cleanup state")
        return state
    except FileNotFoundError:
        return {"pending": False}
    except (OSError, ValueError):
        return {"pending": True, "error": "Não foi possível ler a pendência Wise; saques bloqueados."}

def _finish_wise_cleanup(_services, email: str, password: str) -> dict[str, Any]:
    result = {"wiseDestinationRemoved": False, "payoutPreferenceRestored": False}
    # Repetimos somente a limpeza, nunca a vinculação ou a solicitação de saque.
    delays = (0, 2, 4, 8, 15, 30)
    for attempt, delay in enumerate(delays, 1):
        try:
            _services.save_json(_services._wise_cleanup_path(), {"pending": True, "email": email,
                "automatic": True, "attempt": attempt, "max_attempts": len(delays),
                "message": "Aguardando confirmação de Wise desvinculada e Dots restaurado."})
        except OSError:
            return {**result, "cleanupPending": True}
        if delay:
            _services.time.sleep(delay)
        try:
            result = _services.crowtado.finalizar_wise(email, password)
        except Exception:
            continue
        if result.get("wiseDestinationRemoved") is True and result.get("payoutPreferenceRestored") is True:
            try:
                _services.save_json(_services._wise_cleanup_path(), {"pending": False})
            except OSError:
                break  # Pendência permanece: recuperação pode repetir a limpeza idempotente.
            return {**result, "cleanupPending": False}
    try:
        _services.save_json(_services._wise_cleanup_path(), {"pending": True, "email": email, "automatic": False,
            "attempt": len(delays), "max_attempts": len(delays),
            "message": "A plataforma não confirmou a limpeza após as tentativas automáticas. Saques bloqueados."})
    except OSError:
        pass
    return {**result, "cleanupPending": True}

def _withdraw_wise_flow(_services, email: str, password: str, wise: dict[str, str]) -> dict[str, Any]:
    # Gravado ANTES de qualquer mutação remota, também cobre falha parcial do vínculo.
    _services.save_json(_services._wise_cleanup_path(), {"pending": True, "email": email,
                                    "started_at": _services.time.time()})
    result = {"status": "unknown"}
    stage = "configure_wise"
    try:
        _services.crowtado.configurar_metodo_saque(email, password, "wise", **wise)
        stage = "request_withdrawal"
        result = _services.crowtado.solicitar_link_saque(email, password, expected_method="wise", cleanup_wise=False)
    except Exception as exc:
        # A failure while linking cannot have submitted a withdrawal. Once
        # the withdrawal function is entered, retain the conservative outcome.
        code = getattr(exc, "account_issue_code", None)
        known_codes = {"authentication", "crowtado_account_missing", "rate_limit",
                       "service", "forbidden", "invalid_response", "network", "timeout",
                       "tls", "email_verification", "mail_authentication", "destination_in_use", "payout_pending",
                       "payout_configuration"}
        result = {"status": "not_requested" if stage == "configure_wise" or getattr(exc, "withdrawal_attempted", True) is False else "unknown",
                  "failureStage": stage,
                  "failureCode": code if code in known_codes else "unexpected"}
        http_status = getattr(exc, "http_status", None)
        if type(http_status) is int and 400 <= http_status <= 599:
            result["httpStatus"] = http_status
    finally:
        # Preserve acceptance before recovery waits, including app/connection loss.
        try:
            _services.save_json(_services.config.DATA_DIR / "withdraw_last_result.json", {
                "email": email, "finished_at": _services.time.time(),
                "result": {"status": result.get("status", "unknown"), "cleanupPending": True}})
        except OSError:
            pass
        cleanup = _services._finish_wise_cleanup(email, password)
    result = {**result, **cleanup}
    # Store only classified evidence, never raw API errors or destination data.
    try:
        diagnostic_fields = {"status", "failureStage", "failureCode", "httpStatus",
                             "wiseDestinationRemoved", "payoutPreferenceRestored", "cleanupPending"}
        _services.save_json(_services.config.DATA_DIR / "withdraw_last_result.json", {
            "email": email, "finished_at": _services.time.time(),
            "result": {key: value for key, value in result.items() if key in diagnostic_fields}})
    except OSError:
        pass  # Losing diagnostics must not turn an accepted payout into failure.
    return result

def _load_withdraw_cooldowns_locked(_services) -> None:
    pass  # state writes use declared dependencies
    saved = _services.load_json_state(_services._withdraw_cooldown_path(), {})
    if any(not isinstance(email, str) or not email.strip()
           or not _services._finite_state_number(value) or value < 0
           for email, value in saved.items()):
        _services._invalid_local_state()
    now = _services.time.time()
    # Future evidence just consulted remains authoritative even after loading.
    for email, value in saved.items():
        if value > now:
            _services._WITHDRAW_LAST_REQUEST[email] = max(float(value), _services._WITHDRAW_LAST_REQUEST.get(email, 0.0))
    if _services._WITHDRAW_COOLDOWN_LOADED:
        return
    for email, value in saved.items():
        # A future attempt is unresolved evidence after clock rollback.
        if now - value < _services._WITHDRAW_COOLDOWN_S:
            _services._WITHDRAW_LAST_REQUEST[email] = float(value)
    _services._WITHDRAW_COOLDOWN_LOADED = True

def _load_withdraw_bulk_locked(_services) -> None:
    pass  # state writes use declared dependencies
    saved = _services.load_json_state(_services._withdraw_bulk_path(), {})
    if saved and (not isinstance(saved.get("results"), list)
                  or any(not isinstance(row, dict) for row in saved["results"])
                  or not isinstance(saved.get("state", "idle"), str)
                  or saved.get("state", "idle") not in {"idle", "running", "done", "error", "interrupted"}
                  or any(type(saved.get(field, 0)) is not int or saved.get(field, 0) < 0
                         for field in ("total", "done"))):
        _services._invalid_local_state()
    if _services._WITHDRAW_BULK_LOADED:
        return
    if isinstance(saved, dict) and isinstance(saved.get("results"), list):
        try:
            total = max(0, int(saved.get("total") or 0))
            done = max(0, int(saved.get("done") or 0))
        except (TypeError, ValueError):
            total = done = 0
        state = {"state": saved.get("state", "idle"),
                 "method": saved.get("method"), "message": saved.get("message", ""),
                 "total": total, "done": done,
                 "results": saved["results"],
                 "current": saved.get("current"),
                 "started_at": saved.get("started_at")}
        if state["state"] == "running":
            state["state"] = "interrupted"
            state["message"] = (
                "O aplicativo foi fechado durante o lote. Confira o link da conta em andamento antes de solicitar novamente.")
            _services.save_json(_services._withdraw_bulk_path(), state)
        _services._WITHDRAW_BULK_STATE.update(state)
    _services._WITHDRAW_BULK_LOADED = True

def _save_withdraw_bulk_locked(_services) -> None:
    _services.load_json_state(_services._withdraw_bulk_path(), {})
    _services.save_json(_services._withdraw_bulk_path(), _services._WITHDRAW_BULK_STATE)

def _wise_withdraw_options(_services, body: dict[str, Any]) -> dict[str, str] | None:
    method = body.get("method")
    if method is None:
        return None
    if method == "paypal":
        if body.get("paypal_confirmed") is not True:
            raise ValueError("confirme PayPal antes de solicitar o saque")
        if body.get("legal_name") or body.get("destination_email"):
            raise ValueError("PayPal usa o fluxo de pagamento da Crowtado, sem destino manual")
        return {"method": "paypal"}
    if method != "wise" or body.get("wise_confirmed") is not True:
        raise ValueError("confirme Wise antes de solicitar o saque")
    name = str(body.get("legal_name") or "").strip()
    destination = str(body.get("destination_email") or "").strip()
    if (len(name) < 2 or len(name) > 200 or len(destination) > 254
            or not _services.re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", destination)):
        raise ValueError("informe o nome legal e um e-mail válido da Wise")
    return {"legal_name": name, "destination_email": destination}

def _withdraw_once(_services, email: str, password: str,
                   wise: dict[str, str] | None = None) -> tuple[dict[str, Any], int]:
    # Inclui vínculo, saque e limpeza na mesma seção exclusiva entre contas.
    if not _services._PAYOUT_OPERATION_LOCK.acquire(blocking=False):
        return {"email": email, "ok": False, "error": "aguarde a operação de saque atual terminar"}, 409
    try:
        if _services._wise_cleanup_snapshot().get("pending"):
            return {"email": email, "ok": False, "error": (
                "Há uma limpeza Wise pendente. Use Concluir limpeza Wise antes de qualquer novo saque.")}, 409
        return _services._withdraw_once_locked(email, password, wise)
    finally:
        _services._PAYOUT_OPERATION_LOCK.release()

def _withdraw_paypal_flow(_services, email: str, password: str) -> dict[str, Any]:
    stage = "configure_paypal"
    try:
        _services.crowtado.configurar_metodo_saque(email, password, "paypal")
        stage = "request_withdrawal"
        return _services.crowtado.solicitar_link_saque(email, password, expected_method="paypal")
    except Exception as exc:
        # Once submitted, a lost response must not encourage another POST.
        result = {"status": "not_requested" if stage == "configure_paypal" or getattr(exc, "withdrawal_attempted", True) is False else "unknown",
                  "failureStage": stage, "method": "paypal"}
        code = getattr(exc, "account_issue_code", None)
        if code in {"payout_configuration", "destination_in_use", "payout_pending"}:
            result["failureCode"] = code
        return result

def _withdraw_once_locked(_services, email: str, password: str,
                          wise: dict[str, str] | None = None) -> tuple[dict[str, Any], int]:
    # An unreadable receipt is unresolved evidence, never permission to retry.
    _services.load_json_state(_services.config.DATA_DIR / "withdraw_last_result.json", {})
    _services._load_balances()
    now = _services.time.time()
    with _services._WITHDRAW_LOCK:
        _services._load_withdraw_cooldowns_locked()
        for key, value in list(_services._WITHDRAW_LAST_REQUEST.items()):
            if now - value >= _services._WITHDRAW_COOLDOWN_S:
                del _services._WITHDRAW_LAST_REQUEST[key]
        if email in _services._WITHDRAW_IN_FLIGHT:
            return {"email": email, "ok": False, "error": "já há uma solicitação em andamento"}, 409
        elapsed = now - _services._WITHDRAW_LAST_REQUEST.get(email, 0.0)
        if email in _services._WITHDRAW_LAST_REQUEST and elapsed < 0:
            return {"email": email, "ok": False, "code": "clock_conflict",
                    "error": "O horário local antecede uma solicitação registrada; confira o relógio antes de solicitar novamente."}, 409
        if elapsed < _services._WITHDRAW_COOLDOWN_S:
            wait_s = max(1, _services.math.ceil(_services._WITHDRAW_COOLDOWN_S - elapsed))
            return {"email": email, "ok": False,
                    "error": f"solicitação recente; aguarde {wait_s}s para repetir"}, 429
        # Grave a tentativa antes da chamada externa: após uma queda, evite
        # repetir imediatamente uma solicitação que pode ter sido aceita.
        _services._WITHDRAW_LAST_REQUEST[email] = now
        try:
            _services.save_json(_services._withdraw_cooldown_path(), _services._WITHDRAW_LAST_REQUEST)
        except OSError:
            _services._WITHDRAW_LAST_REQUEST.pop(email, None)
            return {"email": email, "ok": False,
                    "error": "não foi possível proteger o histórico de solicitações"}, 503
        _services._WITHDRAW_IN_FLIGHT.add(email)
    try:
        if wise and wise.get("method") == "paypal":
            result = _services._withdraw_paypal_flow(email, password)
        elif wise:
            result = _services._withdraw_wise_flow(email, password, wise)
        else:
            try:
                result = _services.crowtado.solicitar_link_saque(email, password)
            except Exception as exc:
                if getattr(exc, "withdrawal_attempted", None) is not False:
                    result = {"status": "unknown", "failureStage": "request_withdrawal"}
                else:
                    raise
        balance_saved = True
        if result.get("status") != "not_requested":
            try:
                _services._invalidate_balance_after_withdrawal(email, result)
            except (OSError, _services.JsonStateError):
                # Provider acceptance must never become a failed/retryable withdrawal.
                balance_saved = False
        success = result.get("status") in {"ok", "review_required"}
        message = _services._withdraw_message(email, result)
        amount = result.get("amountCents")
        if type(amount) is int and amount >= 0 and result.get("currency") == "USD":
            message += f"; valor informado pela Crowtado: US$ {amount / 100:.2f}"
        if not balance_saved:
            message += "; não foi possível marcar o saldo anterior como desatualizado. Atualize o saldo antes de continuar; não repita este saque."
        if wise and wise.get("method") != "paypal" and result.get("cleanupPending") is False and result.get("status") != "ok":
            message += "; Wise desvinculada e Dots confirmado."
            if result.get("status") != "not_requested":
                message += " Confira o histórico antes de repetir o saque."
        response = {"ok": success, "email": email, "message": message, "result": result}
        if not success:
            response["error"] = message
        return response, 200 if success else 409
    except Exception as exc:
        if wise and wise.get("method") != "paypal":
            return {"email": email, "ok": False, "error": (
                "O fluxo Wise não foi concluído. Confira o vínculo e o histórico na Crowtado "
                "antes de repetir; nenhuma nova tentativa automática será feita.")}, 400
        if not isinstance(exc, _services.crowtado.CrowtadoError):
            raise
        issue = _services.account_issue(email, exc, stage="Solicitação de saque Crowtado")
        return {"email": email, "ok": False, "code": "withdrawal_not_requested",
                "error": issue["reason"] + " " + issue["action"], "issue": issue}, 400
    finally:
        with _services._WITHDRAW_LOCK:
            _services._WITHDRAW_IN_FLIGHT.discard(email)

def _withdraw_bulk_snapshot(_services) -> dict[str, Any]:
    with _services._WITHDRAW_BULK_LOCK:
        _services._load_withdraw_bulk_locked()
        return {**_services._WITHDRAW_BULK_STATE, "results": list(_services._WITHDRAW_BULK_STATE["results"])}

def _start_withdraw_worker(_services, eligible, wise):
    _services.load_json_state(_services.config.DATA_DIR / "withdraw_last_result.json", {})
    _services._load_balances()
    with _services._WITHDRAW_LOCK:
        _services._load_withdraw_cooldowns_locked()
    with _services._WITHDRAW_BULK_LOCK:
        _services._load_withdraw_bulk_locked()
        if _services._WITHDRAW_BULK_STATE["state"] == "running":
            return ({"error": "já há um saque em lote em andamento"}), 409
        _services._WITHDRAW_BULK_STATE.update(
            state="running", total=len(eligible), done=0, results=[],
            method=wise.get("method", "wise") if wise else "saved",
            current=None, message="", started_at=_services.time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        try:
            _services._save_withdraw_bulk_locked()
        except OSError:
            _services._WITHDRAW_BULK_STATE["state"] = "error"
            return ({"error": "não foi possível salvar o histórico do lote"}), 503
    thread = _services.threading.Thread(target=_services._withdraw_bulk_run, args=(eligible, wise) if wise else (eligible,),
                              daemon=True, name="moneymin-withdraw-bulk")
    try:
        thread.start()
    except RuntimeError:
        with _services._WITHDRAW_BULK_LOCK:
            _services._WITHDRAW_BULK_STATE.update(state="error", current=None,
                message="O processamento não pôde começar; nenhum link foi solicitado.")
            try:
                _services._save_withdraw_bulk_locked()
            except OSError:
                pass
        return ({"error": "não foi possível iniciar o saque em lote"}), 503
    return {"ok": True, "total": len(eligible), "background": True}, 202

def _withdraw_bulk_run(_services, creds: dict[str, str], wise: dict[str, str] | None = None) -> None:
    for email, password in creds.items():
        with _services._WITHDRAW_BULK_LOCK:
            _services._WITHDRAW_BULK_STATE["current"] = email
            try:
                _services._save_withdraw_bulk_locked()
            except OSError:
                _services._WITHDRAW_BULK_STATE.update(
                    state="error", message="Não foi possível salvar o progresso; nenhuma nova conta será solicitada.")
                return
        try:
            result, _ = (_services._withdraw_once(email, password, wise) if wise
                         else _services._withdraw_once(email, password))
        except Exception:  # Sem recibo, o resultado externo pode ser inconclusivo.
            result = {"email": email, "ok": False,
                      "error": "A solicitação não produziu um resultado confirmado. Confira o histórico na Crowtado antes de repetir.",
                      "result": {"status": "unknown", "failureStage": "request_withdrawal"}}
        with _services._WITHDRAW_BULK_LOCK:
            _services._WITHDRAW_BULK_STATE["results"].append({
                "email": email, "ok": result["ok"],
                "message": result.get("message") or result.get("error") or "falha desconhecida",
            })
            _services._WITHDRAW_BULK_STATE["done"] += 1
            _services._WITHDRAW_BULK_STATE["current"] = None
            detail = result.get("result") or {}
            stop_wise = wise and wise.get("method") != "paypal" and not (
                result.get("ok") and detail.get("status") == "ok"
                and detail.get("wiseDestinationRemoved") is True
                and detail.get("payoutPreferenceRestored") is True
                and detail.get("cleanupPending") is not True)
            stop_uncertain = detail.get("status") in {
                "unknown", "dots_pending_retry", "manual_pending_retry",
                "tremendous_pending_retry", "stripe_global_pending_retry"}
            if stop_wise:
                _services._WITHDRAW_BULK_STATE.update(state="error", message=(
                    "Lote Wise interrompido. Confira a última conta: somente após saque aceito, "
                    "Wise desvinculada e Dots confirmado a próxima conta pode começar."))
            elif stop_uncertain:
                _services._WITHDRAW_BULK_STATE.update(state="error", message=(
                    "Lote interrompido: a última solicitação está pendente ou inconclusiva. "
                    "Confira o histórico na Crowtado antes de repetir."))
            try:
                _services._save_withdraw_bulk_locked()
            except OSError:
                _services._WITHDRAW_BULK_STATE.update(
                    state="error", message="O resultado não pôde ser salvo. Confira o link antes de repetir.")
                return
            if stop_wise or stop_uncertain:
                return
    with _services._WITHDRAW_BULK_LOCK:
        _services._WITHDRAW_BULK_STATE["state"] = "done"
        try:
            _services._save_withdraw_bulk_locked()
        except OSError:
            _services._WITHDRAW_BULK_STATE.update(
                state="error", message="O resultado final não pôde ser salvo. Confira os resultados exibidos.")

def _banned_withdraw_eligibility(_services, row: dict[str, Any]) -> tuple[bool, str]:
    """A elegibilidade usa somente a leitura Crowtado, nunca o status Minute."""
    email = str(row.get("email") or "")
    if _services.org_policy.account_kind(email) != "crowtado":
        return False, "Saldo Crowtado não se aplica a esta conta."
    if not row.get("password"):
        return False, "A senha Crowtado não está salva no registro de banidas."
    monitor = row.get("monitor") if isinstance(row.get("monitor"), dict) else {}
    site_check = _services.account_health.provider(monitor, "crowtado")
    if site_check and site_check.get("status") != "active":
        return False, "Crowtado: " + str(site_check.get("status_label") or "verificação inconclusiva") + ". Verifique novamente antes de sacar."
    if monitor.get("withdraw_result_status"):
        return False, str(monitor.get("withdraw_result_detail") or
                          "O saque já foi solicitado ou recusado; atualize o saldo antes de tentar novamente.")
    if monitor.get("balance_status") != "ok" or monitor.get("balance_stale") is not False:
        return False, "Consulte novamente o saldo Crowtado para confirmar o saque."
    if not _services._confirmed_available_balance(monitor.get("balance")):
        return False, "O saldo aprovado na Crowtado deve ser superior a US$ 25,00."
    try:
        checked = _services.datetime.datetime.fromisoformat(str(monitor["balance_updated_at"]))
        age = (_services.datetime.datetime.now(_services.datetime.timezone.utc) - checked.astimezone(_services.datetime.timezone.utc)).total_seconds()
    except (KeyError, TypeError, ValueError, OverflowError):
        return False, "Consulte novamente o saldo Crowtado para confirmar o saque."
    if not 0 <= age < 24 * 3600:
        return False, "A consulta de saldo expirou; atualize antes de solicitar o saque."
    return True, "Saldo disponível confirmado na Crowtado. O saque será revalidado antes da solicitação."

def _remember_banned_withdraw_result(_services, 
    email: str, status: str, detail: str, *, balance: dict[str, Any] | None = None,
    balance_error: bool = False,
) -> None:
    """Atualiza só o estado do saque; a restrição Minute permanece intacta."""
    with _services._PERSISTENCE_LOCK:
        path = _services.config.DATA_DIR / "banned_accounts.json"
        archive = _services.banned_store.load(path, {"accounts": []})
        for row in archive.get("accounts", []):
            if str(row.get("email") or "").strip().casefold() != email:
                continue
            monitor = dict(row.get("monitor") or {})
            monitor["withdraw_result_status"] = status
            monitor["withdraw_result_detail"] = detail
            if balance is not None:
                monitor["balance"] = balance
                monitor["balance_status"] = "ok"
                monitor["balance_stale"] = False
                monitor["balance_updated_at"] = _services.datetime.datetime.now(_services.datetime.timezone.utc).isoformat()
            elif balance_error:
                monitor["balance_status"] = "error"
                monitor["balance_stale"] = True
            row["monitor"] = monitor
            _services.banned_store.save(path, archive)
            return

def _last_withdrawal_receipt(_services, configured: set[str]) -> dict[str, Any]:
    """Expose recorded provider acceptance separately from Wise cleanup.

    Legacy Wise diagnostics are sufficient evidence of acceptance, but never of
    settlement in the recipient's bank. Do not infer success from balance loss.
    """
    record = _services.load_json_state(_services.config.DATA_DIR / "withdraw_last_result.json", {})
    if not isinstance(record, dict) or record.get("email") not in configured:
        return {}
    result = record.get("result")
    if not isinstance(result, dict):
        return {}
    status = result.get("status")
    if not isinstance(status, str):
        return {}
    accepted = status == "ok"
    email = record["email"]
    when = record.get("finished_at")
    if type(when) not in (float, int) or not _services.math.isfinite(when):
        return {}
    try:
        stamp = _services.datetime.datetime.fromtimestamp(when, _services.datetime.timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return {}
    pending = _services._wise_cleanup_snapshot()
    current_pending = bool(pending.get("pending") and
                           (pending.get("email") == email or pending.get("error")))
    if accepted:
        message = "Saque Wise aceito pela Crowtado. Não repita esta solicitação."
    elif status == "review_required":
        message = "Solicitação Wise enviada para revisão da Crowtado."
    else:
        message = _services._withdraw_message(email, result)
    if accepted or status == "review_required":
        if current_pending:
            message += " A limpeza Wise está pendente; novos saques permanecem bloqueados."
        elif result.get("wiseDestinationRemoved") is True and result.get("payoutPreferenceRestored") is True:
            message += " Wise desvinculada e Dots confirmado."
        elif result.get("cleanupPending") is True:
            message += " Houve pendência de limpeza; atualmente não há bloqueio de limpeza registrado."
        message += " O recebimento na Wise não foi verificado pelo QMoney."
    return {"email": email, "accepted": accepted, "status": status,
            "finished_at": stamp, "cleanup_pending": current_pending, "message": message}

def _withdraw_message(_services, email: str, result: dict[str, Any]) -> str:
    """Mensagem curta e segura para o resultado do payouts.withdraw."""
    status = str(result.get("status") or "")
    if result.get("cleanupPending") is True:
        outcome = ("saque aceito" if status == "ok" else
                   "saque enviado para revisão" if status == "review_required" else
                   "resultado do saque: " + (status or "desconhecido"))
        return (f"{email}: {outcome}. LIMPEZA WISE PENDENTE: o fluxo não está concluído. "
                "Novos saques estão bloqueados. Use Concluir limpeza Wise; isso não repete o saque.")
    if status == "ok":
        if result.get("wiseDestinationRemoved") is False:
            restored = result.get("payoutPreferenceRestored") is True
            return (f"saque solicitado com sucesso para {email}; a desvinculação da Wise não foi confirmada. "
                    + ("Dots está como padrão. " if restored else "A volta para Dots também não foi confirmada. ")
                    + "Confira a conta na Crowtado sem repetir o saque; o lote não continuará.")
        if result.get("payoutPreferenceRestored") is False:
            return (f"saque solicitado com sucesso para {email}; não foi possível confirmar "
                    "a volta para Dots. Configure Dots novamente, sem repetir o saque.")
        if result.get("payoutPreferenceRestored") is True:
            return f"saque solicitado com sucesso para {email}; Wise desvinculada e método padrão restaurado para Dots"
        if result.get("dotsEmailDelivery") == "sent":
            return f"link de saque enviado por email para {email}"
        if result.get("dotsSmsDelivery") == "sent":
            return f"link de saque enviado por SMS para a conta {email}"
        if result.get("dotsEmailDelivery") == "already_settled":
            return f"saque solicitado para {email}; cadastro Dots já estava concluído"
        return f"solicitação de saque enviada para {email}"
    if status == "review_required":
        return f"saque de {email} enviado para revisão do Crowtado"
    if status in {"on_hold", "hold"}:
        reason = result.get("holdReason")
        return "Os saques estão retidos: " + (reason[:500] if isinstance(reason, str) and reason else "Confira a restrição no painel da Crowtado.")
    if status == "eligibility_unconfirmed":
        return "Saque não enviado: não foi possível confirmar a elegibilidade no Minute pela Crowtado. Atualize a consulta e tente novamente."
    if status.endswith("_pending_retry"):
        return (f"A Crowtado registrou uma tentativa pendente para {email} e fará nova tentativa. "
                "Não solicite outro saque; acompanhe o histórico da plataforma.")
    if status == "not_requested":
        method = "PayPal" if result.get("method") == "paypal" or result.get("failureStage") == "configure_paypal" else "Wise"
        reasons = {
            "destination_in_use": "o destino está vinculado a outra conta",
            "payout_pending": "há pagamento pendente impedindo a alteração",
            "payout_configuration": "a Crowtado ainda não confirmou a configuração",
        }
        reason = reasons.get(result.get("failureCode"), "a configuração não foi concluída")
        return f"Saque não solicitado por {method}: {reason}."
    messages = {
        "in_transit": "há um pagamento em trânsito que a Crowtado não permite substituir; aguarde a conclusão antes de solicitar outro saque",
        "not_requested": "saque não solicitado: a configuração da Wise não foi concluída",
        "unknown": "resultado do saque inconclusivo; confira o histórico na Crowtado antes de repetir",
        "below_minimum": "saldo abaixo do mínimo para saque",
        "hold": "saques estão temporariamente bloqueados para esta conta",
        "dots_not_ready": "o método Dots ainda não está disponível para esta conta",
        "manual_not_ready": "a Crowtado não confirmou o destino manual; configure Wise antes de solicitar",
        "tremendous_not_ready": "o provedor de pagamento da Crowtado não está disponível para esta conta",
        "stripe_global_not_ready": "o provedor de pagamento ainda não está pronto",
        "tremendous_failed": "o provedor de pagamento recusou a solicitação; confira o histórico na Crowtado",
        "manual_failed": "o pagamento manual falhou; confira o histórico na Crowtado antes de repetir",
        "stripe_global_failed": "o provedor de pagamento recusou a solicitação; confira o histórico na Crowtado",
        "no_balance": "não há saldo disponível para saque",
        "dots_failed": "o Dots recusou a solicitação de saque",
        "dots_pending_retry": "o Crowtado ainda tentará enviar o link novamente",
    }
    return messages.get(
        status, "Resposta de saque não reconhecida. Confira o histórico na Crowtado antes de repetir.")
