"""recovery routes: existing behavior with explicit dependencies."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('HOLO_CACHE_RUNNER', 'RECOVERY', 'RUNNER', '_HEAVY_RUNNER_LOCK', '_list_accounts', '_resolve_org', '_wise_cleanup_snapshot', 'campaign_closing_response', 'campaign_drain', 'jsonify', 'nymeria_library_worker', 're', 'recovery', 'recovery_catalog', 'request')


def recovery_snapshot(_services):
    from ... import recovery
    if _services.request.args.get("async") == "1":
        worker = _services.RECOVERY.snapshot()
        def read(progress):
            progress("Lendo os registros de recuperação desta instalação…")
            try:
                return recovery.snapshot(), 200
            except (ValueError, OSError) as exc:
                from ...recovery_errors import error_response
                return error_response(exc), 409
        # Poll URLs retain refresh=1; only an explicit new read may replace
        # a terminal cached result, not a follow-up carrying its job_id.
        refresh = (_services.request.args.get("refresh") == "1"
                   and "job_id" not in _services.request.args)
        result, status = _services.recovery_catalog.get((worker.get("state"), worker.get("email")), read,
                                              refresh=refresh)
        return _services.jsonify({**result, "wise_cleanup": _services._wise_cleanup_snapshot(), "worker": worker}), status
    try:
        return _services.jsonify({**recovery.snapshot(), "wise_cleanup": _services._wise_cleanup_snapshot(), "worker": _services.RECOVERY.snapshot()})
    except (ValueError, OSError) as exc:
        from ...recovery_errors import error_response
        return _services.jsonify(error_response(exc)), 409

def reconcile_recovery(_services):
    from ... import recovery
    with _services._HEAVY_RUNNER_LOCK:
        if _services.campaign_drain["requested"]:
            return _services.campaign_closing_response()
        if _services.RUNNER.running or _services.RECOVERY.running:
            return _services.jsonify({"error": "Aguarde a campanha encerrar antes de reconciliar seus registros."}), 409
        try:
            return _services.jsonify(recovery.reconcile_confirmed())
        except (ValueError, OSError):
            return _services.jsonify({"error": "A reconciliação não foi concluída. Preserve os registros e tente novamente."}), 409

def resume_recovery(_services):
    body = _services.request.get_json(silent=True) or {}
    email = body.get("email")
    session_id = body.get("session_id")
    if "session_id" in body and (not isinstance(session_id, str)
            or _services.re.fullmatch(r"[A-Za-z0-9_-]{1,160}", session_id) is None):
        return _services.jsonify({"error": "Selecione uma sessão de recuperação válida."}), 400
    if body.get("confirmed") is not True or not isinstance(email, str) or not email:
        return _services.jsonify({"error": "Confirme a conta e a retomada dos envios existentes."}), 400
    with _services._HEAVY_RUNNER_LOCK:
        if _services.campaign_drain["requested"]:
            return _services.campaign_closing_response()
        if _services.RUNNER.running or _services.RECOVERY.running or _services.HOLO_CACHE_RUNNER.running or _services.nymeria_library_worker.running:
            return _services.jsonify({"error": "Aguarde a operação em andamento terminar."}), 409
        try:
            if email not in {account["email"] for account in _services._list_accounts()}:
                return _services.jsonify({"error": "Conecte novamente essa conta antes de retomar."}), 400
            if not any(item["email"] == email and item["can_resume"]
                       and (session_id is None or item["session_id"] == session_id)
                       for item in _services.recovery.snapshot()["items"]):
                return _services.jsonify({"error": "Nenhuma sessão desta conta permite retomada automática; revise o histórico."}), 409
            if session_id is None:
                _services.RECOVERY.start(email, _services._resolve_org)
            else:
                _services.RECOVERY.start(email, _services._resolve_org, session_id=session_id)
        except (ValueError, OSError, RuntimeError):
            return _services.jsonify({"error": "Não foi possível iniciar a recuperação. Os registros foram preservados."}), 409
    return _services.jsonify({"worker": _services.RECOVERY.snapshot()}), 202
