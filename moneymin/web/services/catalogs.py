"""catalogs services: existing behavior with explicit dependencies."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('_archive_checked_ban', '_preflight_fingerprint', '_removed_accounts', 'account_issue', 'banned_store', 'config', 'hashlib', 'json', 'token_store')


def _catalog_account_failure(_services, email: str, error: Exception) -> tuple[dict, int]:
    """Report a remote catalog-access failure without conflating local indexing."""
    issue = _services.account_issue(email, error, stage="Carregamento remoto de categorias")
    body = {"error": issue["reason"] + " " + issue["action"],
            "code": "catalog_account_unavailable", "issue": issue}
    if issue.get("restriction_confirmed") is True:
        archived = {"email": email, "status": "disabled", "issue": issue}
        try:
            _services._archive_checked_ban(email, archived)
        except Exception as exc:
            # Keep the safe account diagnosis, but claim permanent removal only
            # after the existing archive path confirms it completed.
            body["removal_failed"] = True
            body["archive_issue"] = {
                "code": "local_archive_failed",
                "stage": "Arquivamento local",
                "reason": "O arquivo local da restrição não foi concluído.",
                "detail": type(exc).__name__,
            }
            body["error"] += " O arquivamento local não foi concluído; a remoção não foi confirmada."
        if archived.get("permanently_removed") is True:
            body["permanently_removed"] = True
    return body, 400

def _catalog_ban_record_confirms_exclusion(_services, email: str) -> bool:
    """Prove the account was archived and has no replacement token before diagnostic polling."""
    try:
        if _services.token_store.load(_services.config.SECRETS_DIR, email, migrate=False) is not None:
            return False
        archive = _services.banned_store.load(_services.config.DATA_DIR / "banned_accounts.json",
                                    {"schema": 1, "accounts": []})
    except Exception:
        return False
    matches = [row for row in archive.get("accounts", [])
               if isinstance(row, dict) and isinstance(row.get("email"), str)
               and row["email"].strip().casefold() == email.strip().casefold()]
    return (len(matches) == 1
            and matches[0].get("restriction_confirmed") is True
            and matches[0].get("stage") == "Carregamento remoto de categorias")

def _preflight_fingerprint(_services, emails: list[str], *, _include_cached_org: bool = True) -> str:
    """Vincula a prévia à identidade/configuração, não à rotação da sessão."""
    digest = _services.hashlib.sha256()
    for email in sorted(set(emails)):
        path = _services.config.token_path(email)
        digest.update(str(path).encode())
        found = _services.token_store.load(_services.config.SECRETS_DIR, email, migrate=False)
        if found is None:
            digest.update(b"missing")
            continue
        _, token = found
        stable = {"email": _services.token_store.email_key(token["email"]),
                  "subject": token.get("localId") or token.get("user_id") or token.get("uid"),
                  "organization": {key: token[key] for key in
                      (("org_key", "organization_id", "tenantId") if _include_cached_org else
                       ("organization_id", "tenantId")) if key in token}}
        # Remover/revogar as credenciais deve invalidar a prévia; renová-las não.
        identity = {"record": stable,
                    "has_id_token": bool(token.get("idToken") or token.get("id_token")),
                    "has_refresh_token": bool(token.get("refreshToken") or token.get("refresh_token"))}
        digest.update(_services.json.dumps(identity, sort_keys=True, ensure_ascii=False).encode())
    removed = _services._removed_accounts()
    digest.update(_services.json.dumps(sorted(email.strip().casefold() for email in emails
                                    if email.strip().casefold() in removed)).encode())
    return digest.hexdigest()

def _catalog_identity_fingerprint(_services, emails: list[str]) -> str:
    # Resolving an organization fills org_key during this very job. That cache
    # write is not an account change and must not discard its completed result.
    return _services._preflight_fingerprint(emails, _include_cached_org=False)
