"""runtime routes: existing behavior with explicit dependencies."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('BALANCES_RUNNER', 'CampaignStateLeaseError', 'HOLO_CACHE_RUNNER', 'ORG_MIGRATION', 'RECOVERY', 'RUNNER', '_ACCOUNT_OPERATION_LOCK', '_BULK_REGISTER_LOCK', '_BULK_REGISTER_STATE', '_HEAVY_RUNNER_LOCK', '_INTEGRATION_OPERATION_LOCK', '_REGISTRATION_BATCH_RESTORE_BLOCKED', '_REGISTRATION_BATCH_RESTORE_MESSAGE', '_WITHDRAW_IN_FLIGHT', '_list_accounts', '_payout_method_snapshot', '_save_registration_batch', '_storage_snapshot', '_withdraw_bulk_snapshot', 'accelerator_catalog', 'banned_monitor', 'campaign', 'campaign_closing_response', 'campaign_drain', 'campaign_reset', 'campaign_state_lease', 'campaign_verifications', 'ego4d_catalog', 'g', 'hashlib', 'hmac', 'json', 'jsonify', 'local_api_token', 'local_media_inventory', 'local_shutdown', 'math', 'nymeria_catalog', 'nymeria_library_worker', 'original_error', 'original_library_index', 'os', 'platform', 'prepared_library_inventory', 'readiness', 'recovery_catalog', 'request', 'sys', 'task_catalog', 'time')


def authenticate_local_client(_services):
    if _services.local_api_token and not _services.hmac.compare_digest(
            _services.request.headers.get("X-QMoney-Session", "").encode("utf-8"),
            _services.local_api_token.encode("utf-8")):
        return _services.jsonify({"error": "Cliente local não autorizado."}), 401

def guard_local_shutdown(_services):
    if _services.local_shutdown["requested"] and _services.request.method != "GET" and _services.request.path != "/api/local/shutdown":
        return _services.jsonify({"error": "A edição local está encerrando as operações. Aguarde o fechamento."}), 409

def shutdown_local_edition(_services):
    """Keep the service alive until every admitted worker releases state."""
    _services.local_shutdown["requested"] = True
    with _services._HEAVY_RUNNER_LOCK:
        _services.campaign_drain["requested"] = True
        _services.RUNNER.stop()
        _services.HOLO_CACHE_RUNNER.stop()
        _services.BALANCES_RUNNER.stop()
        _services.nymeria_library_worker.stop()
        active_requests = _services.campaign_drain["requests"]
    with _services._BULK_REGISTER_LOCK:
        if _services._BULK_REGISTER_STATE.get("state") == "running":
            _services._BULK_REGISTER_STATE["state"] = "stopping"
            _services._save_registration_batch()
        registration_active = _services._BULK_REGISTER_STATE.get("state") in {"running", "stopping"}
    from ...mail_cleanup import CLEANUP
    CLEANUP.cancel.set()
    loaders = (_services.task_catalog, _services.campaign_verifications, _services.accelerator_catalog,
               _services.recovery_catalog, _services.original_library_index, _services.prepared_library_inventory,
               _services.local_media_inventory, _services.nymeria_catalog, _services.ego4d_catalog)
    runners = (_services.RUNNER, _services.HOLO_CACHE_RUNNER, _services.BALANCES_RUNNER, _services.RECOVERY, _services.nymeria_library_worker)
    runner_active = any(getattr(runner, "running", False) or
                        (getattr(runner, "_thread", None) is not None and runner._thread.is_alive())
                        for runner in runners)
    ready = not (runner_active or any(loader.busy for loader in loaders)
                 or active_requests or registration_active or _services.ORG_MIGRATION.running
                 or _services.banned_monitor.snapshot().get("state") == "running"
                 or CLEANUP.snapshot().get("state") in {"scanning", "moving"}
                 or _services._withdraw_bulk_snapshot().get("state") == "running"
                 or _services._payout_method_snapshot().get("state") == "running"
                 or _services._WITHDRAW_IN_FLIGHT or _services._ACCOUNT_OPERATION_LOCK.locked()
                 or _services._INTEGRATION_OPERATION_LOCK.locked())
    return _services.jsonify({"ok": True, "ready": ready})

def campaign_closing_response(_services):
    return _services.jsonify({"error_code": "campaign_closing", "error": "O aplicativo está encerrando os envios. Nenhuma campanha nova será iniciada."}), 409

def admit_campaign_request(_services):
    scoped = (_services.request.path == "/api/campaigns"
              or _services.request.path.startswith("/api/campaigns/")
              or _services.request.path in {"/api/recovery/resume", "/api/recovery/reconcile"})
    if (not scoped or _services.request.path == "/api/campaigns/drain"
            or _services.request.method not in {"POST", "PUT", "PATCH", "DELETE"}):
        return
    with _services._HEAVY_RUNNER_LOCK:
        if _services.campaign_drain["requested"]:
            return _services.campaign_closing_response()
        if _services.nymeria_library_worker.running and _services.request.path in {
                "/api/campaigns", "/api/campaigns/preflight", "/api/campaigns/original",
                "/api/campaigns/original/preflight", "/api/recovery/resume"}:
            return _services.jsonify({"error_code": "library_preparation_busy",
                            "error": "Aguarde a preparação da Biblioteca terminar antes de iniciar envios."}), 409
        _services.campaign_drain["requests"] += 1
        _services.g.campaign_request_admitted = True

def guard_campaign_state(_services):
    scoped = any(_services.request.path == prefix or _services.request.path.startswith(prefix + "/")
                 for prefix in ("/api/campaigns", "/api/recovery", "/api/logs",
                                "/api/sent", "/api/tasks", "/api/holo-cache", "/api/storage",
                                "/api/library"))
    if not scoped or _services.request.path == "/api/campaigns/drain":
        return
    lease = _services.campaign_state_lease()
    try:
        lease.__enter__()
    except _services.CampaignStateLeaseError as exc:
        code = getattr(exc, "code", "campaign_reset_busy")
        return _services.jsonify({"error_code": code,
                        "error": ("A limpeza local foi interrompida. Use Reset completo novamente antes de iniciar envios."
                                  if code == "campaign_reset_incomplete" else
                                  "A limpeza local está em andamento. Aguarde terminar.")}), 409
    _services.g.campaign_state_lease = lease
    if _services.campaign_reset.pending() and (_services.request.path.startswith("/api/campaigns")
                                    or _services.request.path.startswith("/api/recovery")):
        return _services.jsonify({"error_code": "campaign_reset_incomplete",
                        "error": "A limpeza local foi interrompida. Use Reset completo novamente antes de iniciar envios."}), 409

def release_campaign_state(_services, _exc):
    lease = _services.g.pop("campaign_state_lease", None)
    if lease is not None:
        lease.__exit__(None, None, None)

def release_campaign_request(_services, _exc):
    if _services.g.pop("campaign_request_admitted", False):
        with _services._HEAVY_RUNNER_LOCK:
            _services.campaign_drain["requests"] -= 1

def invalid_secure_store(_services, exc):
    return _services.jsonify({"error": str(exc), "code": "local_vault_unreadable"}), 409

def invalid_banned_store(_services, exc):
    response = _services.jsonify({"error": str(exc), "code": "archived_accounts_unreadable"})
    response.headers["Cache-Control"] = "no-store"
    return response, 409

def invalid_token_identity(_services, exc):
    response = _services.jsonify({"error": "O acesso salvo está inválido ou possui identidade conflitante. Preserve os arquivos e revise o acesso.",
                        "code": "local_token_identity_conflict"})
    response.headers["Cache-Control"] = "no-store"
    return response, 409

def invalid_authoritative_state(_services, _exc):
    response = _services.jsonify({"error": "Não foi possível ler o estado local com segurança. Os arquivos foram preservados; revise ou restaure um backup válido antes de continuar.",
                        "code": "local_state_unreadable"})
    response.headers["Cache-Control"] = "no-store"
    return response, 409

def bound_original_request(_services):
    if _services.request.method == "POST" and _services.request.path in {
            "/api/campaigns/original", "/api/campaigns/original/preflight"}:
        # Bound the raw body before the generic JSON reader consumes it.
        if _services.request.content_length is None or _services.request.content_length > 1024 * 1024:
            return _services.original_error("original_invalid_request")

def validate_json_body(_services):
    if (_services.request.path.startswith("/api/") and _services.request.method in ("POST", "PUT", "PATCH")
            and _services.request.get_data(cache=True)):
        if not _services.request.is_json or not isinstance(_services.request.get_json(silent=True), dict):
            return _services.jsonify({"error": "O corpo da requisição deve ser um objeto JSON válido."}), 400
    if _services.request.method == "POST" and _services.request.path in ("/api/campaigns", "/api/campaigns/preflight"):
        body = _services.request.get_json(silent=True) or {}
        accounts = body.get("accounts", [])
        tasks = body.get("tasks", [])
        if (not isinstance(accounts, list)
                or any(not isinstance(email, str) or not email.strip() for email in accounts)):
            return _services.jsonify({"error": "accounts deve ser uma lista de contas válidas."}), 400
        normalized = [email.strip().casefold() for email in accounts]
        if len(set(normalized)) != len(normalized):
            return _services.jsonify({"error": "A seleção contém contas duplicadas."}), 400
        if not isinstance(tasks, list) or any(not isinstance(task, dict) for task in tasks):
            return _services.jsonify({"error": "tasks deve ser uma lista de categorias válidas."}), 400
        try:
            target = body.get("target_hours", 0)
            if isinstance(target, bool) or not _services.math.isfinite(float(target)):
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            return _services.jsonify({"error": "target_hours deve ser um número finito."}), 400

def guard_account_operations(_services):
    registration_route = (
        _services.request.path in {"/api/accounts/register", "/api/accounts/bulk-register"}
        or _services.request.path.startswith("/api/accounts/") and _services.request.path.endswith("/resume")
    )
    if (_services._REGISTRATION_BATCH_RESTORE_BLOCKED and _services.request.method == "POST"
            and registration_route):
        return _services.jsonify({"error": _services._REGISTRATION_BATCH_RESTORE_MESSAGE,
                        "code": "registration_progress_restore_blocked",
                        "not_admitted": True}), 503
    if _services.request.path.startswith("/api/integrations/") and _services.request.method in ("PUT", "DELETE"):
        _services._INTEGRATION_OPERATION_LOCK.acquire()
        _services.g.integration_operation_locked = True
        with _services._BULK_REGISTER_LOCK:
            if _services._BULK_REGISTER_STATE.get("state") in ("running", "stopping"):
                return _services.jsonify({"error": "Aguarde o cadastro terminar antes de alterar a integração de e-mail."}), 409
    account_write = _services.request.path.startswith("/api/accounts") and _services.request.method != "GET"
    campaign_start = _services.request.path in ("/api/campaigns", "/api/campaigns/preflight",
                                     "/api/campaigns/original", "/api/campaigns/original/preflight") and _services.request.method == "POST"
    synchronous_tasks = ((_services.request.path == "/api/tasks" and _services.request.args.get("async") != "1")
                         or _services.request.path == "/api/campaigns/original/tasks")
    if account_write or campaign_start or synchronous_tasks:
        if not _services._ACCOUNT_OPERATION_LOCK.acquire(blocking=not account_write):
            return _services.jsonify({"error": "Aguarde a operação atual de contas terminar."}), 409
        _services.g.account_operation_locked = True
        with _services._BULK_REGISTER_LOCK:
            registration_command = _services.request.method == "POST" and (
                _services.request.path == "/api/accounts/bulk-register"
                or _services.request.path == "/api/accounts/register" and _services.request.args.get("async") == "1"
                or _services.request.path.startswith("/api/accounts/") and _services.request.path.endswith("/resume"))
            body = _services.request.get_json(silent=True) or {}
            request_id = body.get("request_id")
            if (registration_command and isinstance(request_id, str) and request_id
                    and _services._BULK_REGISTER_STATE.get("request_id") == request_id):
                fingerprint = _services.hashlib.sha256((_services.request.path + _services.json.dumps(body, sort_keys=True)).encode()).hexdigest()
                if _services._BULK_REGISTER_STATE.get("request_fingerprint") != fingerprint:
                    return _services.jsonify({"error": "Este identificador já foi usado com outros dados de cadastro."}), 409
                return _services.jsonify({"ok": True, "request_id": request_id, "existing": True,
                                "total": _services._BULK_REGISTER_STATE.get("total", 0),
                                "domain": _services._BULK_REGISTER_STATE.get("domain", "")})
            if (_services._BULK_REGISTER_STATE.get("state") in ("running", "stopping")
                    and _services.request.path != "/api/accounts/bulk-register/stop"):
                return _services.jsonify({"error": "Aguarde o cadastro em lote terminar."}), 409
        if _services.ORG_MIGRATION.running:
            return _services.jsonify({"error": "Aguarde a migração das organizações terminar."}), 409

def release_account_operation(_services, _error):
    if _services.g.pop("integration_operation_locked", False):
        _services._INTEGRATION_OPERATION_LOCK.release()
    if _services.g.pop("account_operation_locked", False):
        _services._ACCOUNT_OPERATION_LOCK.release()

def index(_services):
    return _services.jsonify({"service": "qmoney", "ui": "qt", "ok": True})

def get_health(_services):
    """Handshake leve: nunca abrir ferramentas, ler contas ou percorrer mídia."""
    return _services.jsonify({
        "ok": True,
        "service": {
            "app_version": _services.os.environ.get("QMONEY_APP_VERSION", "unknown"),
        },
    })

def get_runtime_readiness(_services):
    return _services.jsonify(_services.readiness.runtime_readiness())

def get_diagnostics(_services):
    """Relatório deliberadamente sem emails, tokens, senhas ou URLs privadas."""
    try:
        ready = _services.readiness.campaign_readiness("all")
    except Exception:  # noqa: BLE001 — diagnóstico precisa continuar sem exportar a exceção
        ready = {
            "ready": False,
            "provider": "all",
            "checks": [{
                "name": "Diagnóstico de prontidão",
                "status": "error",
                "detail": "Não foi possível concluir a verificação local.",
            }],
        }
    safe_check_names = {
        "FFmpeg/FFprobe", "Navegador privado", "Contas Minute", "Catálogo HoloAssist",
        "Índices HoloAssist", "Acelerador HoloAssist", "Catálogo Ego4D", "Credenciais Ego4D",
        "Acelerador Ego4D", "Espaço livre", "Criador de contas", "Diagnóstico de prontidão",
    }
    # Details may contain exception messages, private paths or signed URLs.
    # Export structured status only; the local readiness screen keeps its guidance.
    ready = {"ready": ready.get("ready") is True, "provider": "all",
             "details_omitted": True,
             "checks": [{"name": check.get("name") if check.get("name") in safe_check_names else "Verificação local",
                         "status": check.get("status") if check.get("status") in {"ok", "warning", "error"} else "error"}
                        for check in ready.get("checks", []) if isinstance(check, dict)]}
    campaign_state = _services.RUNNER.snapshot()
    return _services.jsonify({
        "schema": 1,
        "generated_at": int(_services.time.time()),
        "service": {
            "app_version": _services.os.environ.get("QMONEY_APP_VERSION", "unknown"),
            "python": _services.platform.python_version(),
            "platform": _services.platform.platform(),
            "frozen": bool(getattr(_services.sys, "frozen", False)),
        },
        "accounts": {"configured": len(_services._list_accounts())},
        "readiness": ready,
        "storage": _services._storage_snapshot(include_path=False),
        "runners": {
            "campaign": {
                "state": campaign_state.get("state"),
                "totals": campaign_state.get("totals"),
                "has_error": bool(campaign_state.get("error")),
            },
            "balances": {"state": _services.BALANCES_RUNNER.state},
            "holo_cache": {"state": _services.HOLO_CACHE_RUNNER.state},
        },
        "history": {"campaign_logs": len(_services.campaign.list_campaign_logs())},
    })
