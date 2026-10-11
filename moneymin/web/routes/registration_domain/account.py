"""Route handlers grouped by account workflow."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('BALANCES_RUNNER', 'JsonStateError', 'RECOVERY', 'RUNNER', '_BULK_REGISTER_LOCK', '_BULK_REGISTER_STATE', '_full_register_account', '_hostinger_is_configured', '_registration_domains', '_valid_registration_name', 'bulk_register_accounts', 'config', 'credential_store', 'jsonify', 'org_policy', 'registration_state', 'request')


def register_account(_services):
    """Cria Crowtado e Minute e conclui o perfil e o vínculo da tarefa."""
    if _services.request.args.get("async") == "1":
        return _services.bulk_register_accounts()
    body = _services.request.get_json(silent=True) or {}
    email = body.get("email", "")
    password = body.get("password", "")
    if not isinstance(email, str) or not isinstance(password, str):
        return _services.jsonify({"error": "email e senha devem ser texto", "code": "invalid_credentials"}), 400
    try:
        email = _services.credential_store.email_key(email)
    except ValueError:
        return _services.jsonify({"error": "E-mail de credencial inválido.",
                        "code": "invalid_credentials"}), 400
    if not password or len(password) > 4096:
        return _services.jsonify({"error": "Informe uma senha válida.",
                        "code": "invalid_credentials"}), 400
    from datetime import date
    from moneymin import crowtado
    month, year = body.get("birth_month"), body.get("birth_year")
    today = date.today()
    if (("birth_month" in body and (type(month) is not int or not 1 <= month <= 12))
            or ("birth_year" in body and (type(year) is not int or not 1900 <= year <= today.year))
            or (type(month) is int and type(year) is int and (year, month) > (today.year, today.month))
            or ("gender" in body and body.get("gender") not in crowtado.GENDERS)):
        return _services.jsonify({"error": "Informe mês, ano de nascimento e gênero válidos para o cadastro.",
                                  "code": "invalid_identity"}), 400
    nome = body.get("nome", email.split("@", 1)[0])
    sobrenome = body.get("sobrenome", "")
    if not _services._valid_registration_name(nome) or not _services._valid_registration_name(sobrenome):
        return _services.jsonify({"error": "Nome e sobrenome devem ser textos de até 200 caracteres, sem controles.",
                        "code": "invalid_identity"}), 400
    if _services.org_policy.account_kind(email) != "claru" and not _services._hostinger_is_configured():
        return _services.jsonify({"error": "Configure a integração Hostinger (domínio catch-all) antes de criar contas."}), 409
    domain = email.split("@", 1)[1]
    configured_domains = {row["domain"].strip().casefold()
                          for row in _services._registration_domains()}
    if _services.org_policy.account_kind(email) != "claru" and domain not in configured_domains:
        return _services.jsonify({"error": "Selecione um domínio catch-all configurado nas integrações."}), 400
    from moneymin import registration_proxy
    proxy_id = body.get("proxy_id", "")
    try:
        registration_proxy.validate_selection(proxy_id)
    except (OSError, ValueError):
        return _services.jsonify({"error": "Selecione um proxy disponível ou importe o arquivo TXT."}), 400
    identity_data: dict[str, Any] = {
        "nome": nome, "sobrenome": sobrenome,
        "email": email, "senha": password,
        "use_referral": body.get("use_referral", True),
        "proxy_id": proxy_id,
        **{key: body[key] for key in ("birth_month", "birth_year", "gender") if key in body},
    }
    if type(identity_data["use_referral"]) is not bool:
        return _services.jsonify({"error": "use_referral deve ser booleano."}), 400
    if _services.RUNNER.running or _services.RECOVERY.running or _services.BALANCES_RUNNER.running:
        return _services.jsonify({"error": "Pare a operação atual antes de criar contas."}), 409
    try:
        result = _services._full_register_account(email, password, identity_data)
    except (OSError, _services.JsonStateError):
        try:
            saved_steps = _services.registration_state.load().get(email, {}).get("steps", {})
        except (OSError, _services.JsonStateError):
            saved_steps = {}
        partial = False
        try:
            partial = _services.credential_store.record_path(_services.config.SECRETS_DIR, email).is_file()
        except OSError:
            pass
        return _services.jsonify({"ok": False, "email": email, "steps": saved_steps,
                        "partial": partial,
                        "code": "local_registration_storage_failure",
                        "error": "O armazenamento local não confirmou o progresso do cadastro. Confira a conta incompleta e retome o mesmo e-mail; as credenciais e etapas já salvas foram preservadas."}), 503
    ok = result["error"] is None
    if result.get("code") == "rate_limit":
        return _services.jsonify({"ok": False, "email": email, **result}), 429
    return _services.jsonify({"ok": ok, "email": email, "partial": result.get("partial", False), "steps": result["steps"],
                     "error": result["error"]}), 200 if ok else 400

def list_claru_accounts(_services):
    from moneymin import claru, claru_registration
    try:
        return _services.jsonify({"accounts": claru.public_accounts(), "pending": claru_registration.public_pending(),
                        "invite_code": _services.config.CLARU_INVITE_CODE})
    except (OSError, ValueError, RuntimeError):
        return _services.jsonify({"error": "Não foi possível ler o cofre Claru. Preserve o arquivo e revise o armazenamento."}), 503

def connect_claru_account(_services):
    from moneymin import claru
    if _services.RUNNER.running or _services.RECOVERY.running or _services.BALANCES_RUNNER.running:
        return _services.jsonify({"error": "Aguarde a operação em andamento antes de alterar a conexão Claru."}), 409
    with _services._BULK_REGISTER_LOCK:
        if _services._BULK_REGISTER_STATE.get("state") in ("running", "stopping"):
            return _services.jsonify({"error": "Aguarde o cadastro em andamento."}), 409
    body = _services.request.get_json(silent=True) or {}
    try:
        row = claru.connect(body.get("email"), body.get("password") or None)
        return _services.jsonify({"ok": True, "account": row, "invite_code": _services.config.CLARU_INVITE_CODE})
    except (ValueError, claru.ClaruError) as exc:
        return _services.jsonify({"error": str(exc)}), 400
    except (OSError, RuntimeError):
        return _services.jsonify({"error": "Não foi possível confirmar e salvar a conexão Claru no cofre local."}), 503
