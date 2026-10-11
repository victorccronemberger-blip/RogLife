"""Route handlers grouped by account workflow."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('AuthError', 'BALANCES_RUNNER', 'CROWTADO_PW_PATH', 'JsonStateError', 'RECOVERY', 'RUNNER', '_PERSISTENCE_LOCK', '_crowtado_creds', '_list_accounts', '_load_prefs', '_mark_bulk_account_removed', '_remove_account_data', '_removed_accounts', '_removed_accounts_path', '_resolve_org', '_save_crowtado_cred', '_save_prefs', '_set_account_removed', 'account_bans', 'account_issue', 'account_transfer', 'config', 'credential_store', 'jsonify', 'login', 'org_policy', 'request', 'token_store')


def import_accounts(_services):
    if _services.request.content_length and _services.request.content_length > _services.account_transfer.MAX_BYTES * 2:
        return _services.jsonify({"error": "Arquivo muito grande; limite de 10 MB."}), 413
    body = _services.request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("content"), str) or type(body.get("apply", False)) is not bool:
        return _services.jsonify({"error": "Informe o conteúdo JSON e uma opção de importação válida."}), 400
    if body.get("apply") and (_services.RUNNER.running or _services.BALANCES_RUNNER.running):
        return _services.jsonify({"error": "Aguarde a campanha ou consulta de saldos terminar antes de importar."}), 409
    try:
        with _services._PERSISTENCE_LOCK:
            result = _services.account_transfer.import_accounts(body["content"], apply=body.get("apply", False),
                passwords_path=_services.CROWTADO_PW_PATH, removed_path=_services._removed_accounts_path())
        return _services.jsonify(result)
    except (_services.JsonStateError, _services.token_store.TokenStoreError):
        raise
    except ValueError as exc:
        return _services.jsonify({"error": str(exc)}), 400
    except OSError:
        return _services.jsonify({"error": "Não foi possível acessar os arquivos locais das contas."}), 500

def export_accounts(_services):
    body = _services.request.get_json(silent=True)
    if not isinstance(body, dict) or ("emails" in body and not isinstance(body["emails"], list)):
        return _services.jsonify({"error": "Seleção de contas inválida."}), 400
    try:
        with _services._PERSISTENCE_LOCK:
            accounts = _services._list_accounts()
            selected = ({str(email).strip().casefold() for email in body.get("emails", [])}
                        if body.get("emails") is not None else
                        {str(account["email"]).strip().casefold() for account in accounts})
            credentials = _services._crowtado_creds()
            corrupt = []
            for account in accounts:
                account_email = str(account["email"]).strip()
                key = account_email.casefold()
                if key not in selected or _services.org_policy.account_kind(account_email) == "claru":
                    continue
                try:
                    individual = _services.credential_store.lookup(
                        _services.config.SECRETS_DIR, account_email, strict=True)
                except ValueError:
                    corrupt.append(account_email)
                    continue
                if individual:
                    credentials[key] = individual
            if corrupt:
                return _services.jsonify({
                    "error": "Exportação cancelada: há credencial local corrompida; os arquivos foram preservados para recuperação.",
                    "accounts": sorted(corrupt),
                }), 409
            missing = sorted(
                str(account["email"]).strip() for account in accounts
                if str(account["email"]).strip().casefold() in selected
                and _services.org_policy.account_kind(str(account["email"])) != "claru"
                and not credentials.get(str(account["email"]).strip().casefold())
            )
            if missing:
                return _services.jsonify({
                    "error": "Exportação cancelada: há conta Crowtado sem senha local; nenhum backup incompleto foi criado.",
                    "accounts": missing,
                }), 409
            result = _services.account_transfer.export_accounts(body.get("emails"), credentials, _services._removed_accounts())
        response = _services.jsonify(result)
        response.headers["Cache-Control"] = "no-store"
        return response
    except (_services.JsonStateError, _services.token_store.TokenStoreError):
        raise
    except ValueError as exc:
        return _services.jsonify({"error": str(exc)}), 400
    except OSError:
        return _services.jsonify({"error": "Não foi possível ler as contas para exportação."}), 500

def add_account(_services):
    body = _services.request.get_json(silent=True) or {}
    email = body.get("email", "")
    password = body.get("password", "")
    if not isinstance(email, str) or not isinstance(password, str):
        return _services.jsonify({"error": "email e senha devem ser texto", "code": "invalid_credentials"}), 400
    email = email.strip()
    if not email or not password:
        return _services.jsonify({"error": "informe email e senha"}), 400
    try:
        _services.account_bans.require_not_banned(email)
    except ValueError as exc:
        return _services.jsonify({"error": str(exc)}), 400
    try:
        _services.login(email, password)
    except (RuntimeError, OSError, _services.AuthError) as exc:
        issue = _services.account_issue(email, exc, stage="Login Minute")
        return _services.jsonify({"error": issue["reason"] + " " + issue["action"],
                        "code": "account_login_failed", "issue": issue}), 400
    try:
        # Reconectar também é um gate de organização: uma conta Crowtado
        # antiga só volta a ficar ativa depois de confirmar PE8EAR5V.
        _services._resolve_org(email)
    except (RuntimeError, OSError, _services.AuthError) as exc:
        issue = _services.account_issue(email, exc, stage="Confirmação de organização Minute")
        return _services.jsonify({"error": issue["reason"] + " " + issue["action"],
                        "code": "account_organization_unconfirmed", "issue": issue}), 400
    try:
        _services._save_crowtado_cred(email, password)
        _services._set_account_removed(email, False)
    except (OSError, ValueError):
        return _services.jsonify({"ok": False, "partial": True,
                        "error": "O acesso foi validado, mas o salvamento local não foi concluído. Confira o armazenamento e o backup das credenciais antes de tentar novamente."}), 500
    return _services.jsonify({"ok": True, "email": email})

def remove_account(_services, email: str):
    if _services.RUNNER.running or _services.RECOVERY.running:
        return _services.jsonify({"error": "pare a campanha antes de remover uma conta"}), 409
    if _services.BALANCES_RUNNER.running:
        return _services.jsonify({"error": "aguarde a consulta de saldos terminar"}), 409
    try:
        normalized = _services.account_transfer.email_key(email)
    except ValueError as exc:
        return _services.jsonify({"error": str(exc)}), 400
    found = _services.token_store.load(_services.config.SECRETS_DIR, normalized, migrate=False)
    credential_path = _services.credential_store.record_path(_services.config.SECRETS_DIR, normalized)
    if found is None and not credential_path.exists():
        return _services.jsonify({"error": f"conta não encontrada: {email}"}), 404
    # Grave primeiro a intenção. Mesmo se o Windows interromper a remoção
    # física, a conta já não reaparece nem volta para uma campanha.
    _services._set_account_removed(email, True)
    try:
        _services.token_store.delete(_services.config.SECRETS_DIR, normalized)
        with _services._PERSISTENCE_LOCK:
            prefs = _services._load_prefs()
            normalized = email.casefold()
            org_keys = prefs.get("org_keys", {})
            if isinstance(org_keys, dict):
                for key in list(org_keys):
                    if str(key).casefold() == normalized:
                        org_keys.pop(key, None)
            selected = prefs.get("selected_accounts")
            if isinstance(selected, list):
                prefs["selected_accounts"] = [
                    value for value in selected
                    if str(value).casefold() != normalized
                ]
            _services._save_prefs(prefs)
        _services._remove_account_data(email)
    except OSError as exc:
        _services._mark_bulk_account_removed(normalized)
        return _services.jsonify({
            "ok": True,
            "warning": f"a conta não voltará, mas alguns dados locais aguardam nova tentativa de limpeza: {exc}",
        })
    _services._mark_bulk_account_removed(normalized)
    return _services.jsonify({"ok": True})
