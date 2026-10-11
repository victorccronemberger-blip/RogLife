"""Protected queue for registering Minute accounts of existing Claru users."""
from __future__ import annotations
import threading
from . import claru, config, credential_store, secure_store

_LOCK = threading.RLock()


def _load():
    data = secure_store.load_secure_settings(config.SECRETS_DIR / "claru_minute_registrations.dat", strict=True)
    if not data:
        return {}
    try:
        if set(data) != {"schema", "accounts"} or type(data["schema"]) is not int or data["schema"] != 1 or not isinstance(data["accounts"], dict):
            raise ValueError
        for key, row in data["accounts"].items():
            if (not isinstance(row, dict) or set(row) != {"login_email", "claru_password", "minute_password", "stage", "minute_email", "proxy_id"}
                    or credential_store.email_key(key) != key or row["login_email"] != key
                    or row["stage"] not in {"prepared", "profile", "complete"} or not isinstance(row["proxy_id"], str)
                    or not isinstance(row["minute_password"], str) or not 8 <= len(row["minute_password"]) <= 4096
                    or (row["claru_password"] is not None and (not isinstance(row["claru_password"], str) or not 1 <= len(row["claru_password"]) <= 4096))
                    or not isinstance(row["minute_email"], str)
                    or (row["minute_email"] and claru._minute_email(row["minute_email"]) != row["minute_email"])):
                raise ValueError
        return data["accounts"]
    except (ValueError, TypeError, claru.ClaruError):
        raise claru.ClaruError("O cofre de cadastros Minute do Claru está inválido. Preserve o arquivo antes de continuar.") from None


def _save(rows):
    secure_store.save_secure_settings(config.SECRETS_DIR / "claru_minute_registrations.dat", {"schema": 1, "accounts": rows})


def public_pending():
    with _LOCK:
        return [{k: row[k] for k in ("login_email", "minute_email", "stage")}
                for row in _load().values() if row["stage"] != "complete"]


def prepare(accounts, proxy_id):
    if not isinstance(accounts, list) or not 1 <= len(accounts) <= 50:
        raise ValueError("Informe de 1 a 50 contas Claru existentes no lote Minute.")
    with _LOCK:
        rows = _load()
        connections = {r["login_email"]: r for r in claru.public_accounts()}
        planned, seen = [], set()
        for item in accounts:
            if not isinstance(item, dict) or set(item) - {"login_email", "claru_password", "minute_password"}:
                raise ValueError("Cada conta deve conter login_email e minute_password; claru_password é opcional para conexões salvas.")
            email = credential_store.email_key(item.get("login_email"))
            if email.rsplit("@", 1)[1] in {"supply.claru.ai", "staging-supply.claru.ai"} or email in seen:
                raise ValueError("Use logins Claru distintos; o e-mail gerado pertence somente ao Minute.")
            seen.add(email)
            previous = rows.get(email)
            if previous:
                if any(p in item and item[p] != previous[p] for p in ("claru_password", "minute_password")):
                    raise ValueError("Uma conta do lote já possui outras senhas protegidas. Retome com as credenciais salvas.")
                row = dict(previous)
            else:
                if not isinstance(item.get("minute_password"), str) or not 8 <= len(item["minute_password"]) <= 4096:
                    raise ValueError("Informe a senha Minute, com pelo menos 8 caracteres, para cada conta.")
                password = item.get("claru_password")
                if password is not None and (not isinstance(password, str) or not 1 <= len(password) <= 4096):
                    raise ValueError("Senha Claru inválida.")
                if not password and email not in connections:
                    raise ValueError("Conecte o perfil Claru ou informe sua senha para confirmar o endereço gerado.")
                row = {"login_email": email, "claru_password": password or None, "minute_password": item["minute_password"],
                       "stage": "prepared", "minute_email": connections.get(email, {}).get("minute_email", ""), "proxy_id": proxy_id}
            planned.append(row)
        rows.update({r["login_email"]: r for r in planned})
        _save(rows)
        saved = _load()
        return [dict(saved[r["login_email"]]) for r in planned]


def checkpoint(email, **changes):
    with _LOCK:
        rows = _load()
        rows[email].update(changes)
        _save(rows)


def connect(row):
    connection = claru.connect(row["login_email"], row["claru_password"], expected_minute=row["minute_email"] or None)
    checkpoint(row["login_email"], minute_email=connection["minute_email"], stage="profile")
    return connection
