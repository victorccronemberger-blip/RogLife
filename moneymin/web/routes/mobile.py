"""Mobile-device and App Check routes, separate from provider setup."""
from __future__ import annotations

REQUIRES = ("jsonify", "request")


def get_android_device(_services):
    """Read public USB diagnostics without touching app data or credentials."""
    from ...integrations import android_diagnostics

    query = _services.request.args
    if set(query) - {"serial"} or len(query.getlist("serial")) > 1:
        return _services.jsonify({"error": "Informe somente um aparelho para o diagnóstico USB."}), 400
    return _services.jsonify(android_diagnostics.snapshot(query.get("serial") or None))


def get_appcheck_status(_services):
    """Read status only: visible panels poll this endpoint automatically."""
    from ... import appcheck

    return _services.jsonify(appcheck.app_check_status())


def recheck_appcheck_integration(_services):
    """Explicit operator action; never return credentials or force past cooldown."""
    from ... import appcheck
    from ... import roglife_appcheck as roglife

    body = _services.request.get_json(silent=True)
    if not isinstance(body, dict) or body:
        return _services.jsonify({"error": "A rechecagem não recebe credenciais ou parâmetros."}), 400
    # Atualizar na Nova campanha: ativa serial/SOURCE e sempre relê o telefone.
    roglife.ensure_device_defaults_for_writes()
    status = appcheck.app_check_status()
    hard_refresh = False
    if status["configured"] and not status["paused"] and not status["retry_after_seconds"]:
        # Live ADB stream from the phone first (bypass in-process JWT). Restarting
        # Minute (force) can take several minutes; only when the live token is unusable.
        appcheck.mint_app_check_token(force_refresh=False, bypass_live_session=True)
        status = appcheck.app_check_status()
        if not status.get("token_available"):
            hard_refresh = True
            appcheck.mint_app_check_token(force_refresh=True, bypass_live_session=True)
            status = appcheck.app_check_status()
    diagnostic = status.get("diagnostic") or {}
    device = status.get("device") if isinstance(status.get("device"), dict) else {}
    left_s = device.get("left_s")
    if status["paused"]:
        message = "As emissões App Check estão pausadas nesta instalação."
    elif status["retry_after_seconds"]:
        message = f"Aguarde {status['retry_after_seconds']} segundos antes de rechecar."
    elif status["token_available"]:
        if isinstance(left_s, (int, float)) and left_s > 0:
            minutes = int(left_s) // 60
            seconds = int(left_s) % 60
            message = (
                f"Token App Check disponível ({minutes}m {seconds}s restantes"
                f"{', após reinício do Minute' if hard_refresh else ' via ADB'}). "
                "A aceitação do CREATE ainda depende do Minute."
            )
        else:
            message = (
                "Token App Check disponível. "
                "A aceitação do CREATE ainda depende do Minute."
            )
    elif not status["configured"]:
        message = "Não há configuração App Check completa nesta instalação."
    else:
        message = diagnostic.get("message") or (
            "A rechecagem não disponibilizou um token App Check. "
            "Se o Minute foi reiniciado, a emissão Play Integrity pode levar alguns minutos."
        )
    return _services.jsonify({"ok": status["token_available"], "message": message, "appcheck": status})
