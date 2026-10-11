"""Route handlers grouped by account workflow."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('BALANCES_RUNNER', 'ORG_MIGRATION', 'RECOVERY', 'RUNNER', '_list_accounts', '_migrate_account_org', 'jsonify', 'request')


def get_org_migration(_services):
    response = _services.jsonify(_services.ORG_MIGRATION.snapshot())
    response.headers["Cache-Control"] = "no-store"
    return response

def start_org_migration(_services):
    if _services.RUNNER.running or _services.RECOVERY.running or _services.BALANCES_RUNNER.running:
        return _services.jsonify({"error": "Aguarde a campanha ou consulta de saldos terminar antes de migrar."}), 409
    body = _services.request.get_json(silent=True)
    if not isinstance(body, dict):
        return _services.jsonify({"error": "Seleção de contas inválida."}), 400
    known = {item["email"].strip().casefold(): item["email"] for item in _services._list_accounts()}
    selected = body.get("emails", list(known))
    if not isinstance(selected, list) or not selected or any(
            not isinstance(email, str) or email.strip().casefold() not in known for email in selected):
        return _services.jsonify({"error": "Selecione contas cadastradas no QMoney."}), 400
    emails = list(dict.fromkeys(known[email.strip().casefold()] for email in selected))
    try:
        result = _services.ORG_MIGRATION.start(emails, _services._migrate_account_org)
    except RuntimeError as exc:
        return _services.jsonify({"error": str(exc)}), 409
    except OSError:
        return _services.jsonify({"error": "Não foi possível salvar o relatório da migração."}), 500
    return _services.jsonify(result), 202
