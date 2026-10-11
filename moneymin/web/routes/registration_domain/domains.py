"""Route handlers grouped by account workflow."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('_REGISTRATION_BATCH_RESTORE_BLOCKED', '_REGISTRATION_BATCH_RESTORE_MESSAGE', '_hostinger_is_configured', '_preflight_checks', '_recover_hostinger_domains', '_registration_domains', 'jsonify', 'registration_limits', 'request')


def list_account_domains(_services):
    """Domínios disponíveis para o criador de contas (perfis Hostinger)."""
    warnings = _services._recover_hostinger_domains()
    domains = _services._registration_domains()
    return _services.jsonify({
        "domains": domains,
        "webmail_url": "https://webmail.hostinger.com" if domains else "",
        "hostinger_configured": _services._hostinger_is_configured(),
        "warning": " ".join(warnings),
    })

def bulk_register_preflight(_services):
    """Valida todas as dependências antes de iniciar a criação em lote."""
    if _services._REGISTRATION_BATCH_RESTORE_BLOCKED:
        return _services.jsonify({"ready": False, "checks": {
            "registration_progress": {"ok": False,
                "detail": _services._REGISTRATION_BATCH_RESTORE_MESSAGE}}})
    blocked = _services.registration_limits.check_all()
    if blocked:
        provider, remaining = blocked
        return _services.jsonify({"ready": False, "checks": {"registration_limit": {
            "ok": False, "detail": f"{provider.title()} limitou o cadastro. Aguarde {remaining} segundo(s)."}},
            "retry_after_seconds": remaining})
    from moneymin import registration_proxy
    selection = _services.request.args.get("proxy_id", "")
    try:
        registration_proxy.validate_selection(selection)
        proxy = registration_proxy.selected(selection)
        with registration_proxy.route(proxy):
            ip = registration_proxy.check_exit_ip() if proxy else None
            result = _services._preflight_checks(_services.request.args.get("domain", ""))
            if proxy:
                result["checks"]["proxy"] = {"ok": True, "detail": f"IP de saída {ip}"}
            return _services.jsonify(result)
    except (OSError, ValueError, RuntimeError):
        return _services.jsonify({"ready": False, "checks": {"proxy": {"ok": False, "detail": "Proxy indisponível; confira a seleção e a conexão."}}})
