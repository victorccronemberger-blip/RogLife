"""Account-domain implementation; invoked with explicit dependency bindings."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Any

REQUIRES = ('CROWTADO_PW_PATH', '_PERSISTENCE_LOCK', '_crowtado_creds', '_invalid_local_state', '_list_accounts', 'config', 'credential_store', 'decode_jsonl_history', 'load_json_state', 'org_policy')


def _crowtado_creds(_services, *, include_individual: bool = True) -> dict[str, str]:
    """Reutiliza credenciais da mesma identidade, inclusive lotes de criação."""
    creds: dict[str, str] = {}
    def collect(rec):
        if not isinstance(rec, dict):
            return
        email = rec.get("email")
        password = rec.get("password") or rec.get("senha")
        if isinstance(email, str) and isinstance(password, str) and password:
            creds[email.strip().casefold()] = password

    for path in sorted(_services.config.DATA_DIR.glob("novas_contas_*.json")):
        rows = _services.load_json_state(path, [], expected_type=list)
        if isinstance(rows, list):
            for rec in rows:
                collect(rec)
    contas = _services.config.DATA_DIR / "contas.jsonl"
    try:
        # Complete strict preflight before any source can be promoted. An
        # unreadable tail leaves the latest known password uncertain.
        for rec in _services.decode_jsonl_history(contas.read_bytes(), expected_type=dict):
            collect(rec)
    except FileNotFoundError:
        pass
    except OSError:
        _services._invalid_local_state()
    stored = _services.load_json_state(_services.CROWTADO_PW_PATH, {})
    if isinstance(stored, dict):
        creds.update({str(email).strip().casefold(): password
                      for email, password in stored.items()
                      if isinstance(password, str) and password})
    # Registros individuais são a fonte primária para credenciais novas. Eles
    # não competem entre contas durante uma gravação e vencem dados legados.
    if include_individual:
        creds.update(_services.credential_store.load_all(_services.config.SECRETS_DIR))
    return creds

def _saved_account_password(_services, email: str, legacy: dict[str, str] | None = None) -> str | None:
    """Consulta legado somente se faltar o registro individual válido.

    Callers com um mapa legado pronto mantêm o contrato anterior. Um registro
    primário corrompido veta o fallback; nunca consulta o merge por esse erro.
    """
    try:
        individual = _services.credential_store.lookup(_services.config.SECRETS_DIR, email, strict=True)
    except ValueError:
        return None
    if individual is not None:
        return individual
    sources = _services._crowtado_creds() if legacy is None else legacy
    return sources.get(email.strip().casefold())

def _configured_crowtado_creds(_services) -> dict[str, str]:
    """Resolve somente acessos Crowtado ativos e promove cópias legadas.

    Contas Claru continuam cadastradas no QMoney, mas não possuem saldo
    Crowtado. Credenciais legadas válidas são copiadas para o registro
    individual atômico sem deixar uma falha de migração interromper a consulta
    que ainda pode usar a cópia antiga.
    """
    saved = {str(e).strip().casefold(): p for e, p in _services._crowtado_creds().items()}
    configured: dict[str, str] = {}
    for account in _services._list_accounts():
        email = str(account["email"])
        if _services.org_policy.account_kind(email) != "crowtado":
            continue
        key = email.strip().casefold()
        try:
            individual = _services.credential_store.lookup(_services.config.SECRETS_DIR, key, strict=True)
        except ValueError:
            # Falha fechada: não autentica saldos/saques com fallback antigo
            # quando a fonte primaria existe, mas perdeu integridade.
            continue
        password = individual or saved.get(key)
        if password:
            configured[email] = password
            if individual is None:
                try:
                    with _services._PERSISTENCE_LOCK:
                        _services.credential_store.save(_services.config.SECRETS_DIR, key, password)
                except (OSError, ValueError):
                    # A cópia legada permanece utilizável. A promoção será
                    # tentada novamente sem transformar a conta em desconectada.
                    pass
    return configured

def _save_crowtado_cred(_services, email: str, password: str) -> None:
    with _services._PERSISTENCE_LOCK:
        # A fonte individual protegida é relida antes de continuar. Arquivos
        # legados continuam sendo apenas fontes de leitura/migração; criar um
        # espelho em JSON anularia a proteção da senha por DPAPI.
        _services.credential_store.save(_services.config.SECRETS_DIR, email, password)
