"""Local admission advice from explicit CREATE receipts, never from mint failure."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Iterable

_CREATE_REJECTION = re.compile(
    r"POST /uploads falhou \((?:401|403|429)\):[^\n]*\[bloqueio: app_check\]"
)
_UPLOAD_ID = re.compile(r"[A-Za-z0-9_-]{1,160}")
_GUIDANCE = (
    "O último CREATE desta conta e organização foi recusado pelo Minute por App Check. "
    "Não há token App Check disponível; nenhum preparo ou envio foi iniciado. "
    "Use Integrações → Rechecar App Check após corrigir a autorização ou configuração. "
    "Os registros dos envios anteriores foram preservados."
)
_UNAVAILABLE = (
    "Não há token App Check disponível; a escrita no Minute exige essa credencial. "
    "Nenhum preparo ou envio foi iniciado. "
    "Use Integrações → Rechecar App Check após corrigir a autorização ou configuração."
)
_DEVICE_DISABLED = (
    "O modo App Check do aparelho está desligado nesta sessão "
    "(QMONEY_APPCHECK_SOURCE/DEVICE). A escrita no Minute exige Play Integrity "
    "via ADB. Remova o desligamento explícito ou abra pelo QMoney local; "
    "nenhum preparo ou envio foi iniciado."
)


def rejected_create_owners(accounts: Iterable, journals: Iterable[dict]) -> set[str]:
    """Scope historical evidence to owner/org; a later receipt clears that refusal."""
    owners = {(account.email.strip().casefold(), account.org_key) for account in accounts}
    latest: dict[tuple[str, str], tuple[float, bool]] = {}
    for row in journals:
        owner = row.get("account_email")
        if not isinstance(owner, str):
            continue
        key = (owner.strip().casefold(), row.get("org_key"))
        if key not in owners:
            continue
        upload_id = row.get("upload_id")
        accepted = isinstance(upload_id, str) and bool(_UPLOAD_ID.fullmatch(upload_id))
        rejected = (not upload_id and row.get("phase") == "create"
                    and row.get("state") == "failed"
                    and isinstance(row.get("error"), str)
                    and bool(_CREATE_REJECTION.search(row["error"])))
        if not accepted and not rejected:
            continue
        try:
            timestamp = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                continue
            when = timestamp.timestamp()
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
            continue
        previous = latest.get(key)
        if previous is None or when > previous[0] or (when == previous[0] and rejected):
            latest[key] = when, bool(rejected)
    return {owner for (owner, _org), (_when, rejected) in latest.items() if rejected}


def campaign_create_blocker(accounts: Iterable) -> str | None:
    """Block campaign start when writes would fail the App Check gate.

    Renews App Check first when remaining life is below the campaign floor so
    a stale local cache does not block start before per-video delivery runs.
    After that attempt, missing token blocks admission the same way the write
    gate blocks POST/PUT/PATCH — before expensive preparation. A prior CREATE
    App Check refusal adds scoped account guidance when journals exist.
    No inferred organization policy, header mutation, or receipt deletion.
    """
    from .. import appcheck, upload
    from ..roglife_appcheck import provider as roglife

    accounts = list(accounts)
    if not accounts:
        return None
    # Bare CLI often omits SOURCE; activate device defaults unless explicitly off.
    activation = roglife.ensure_device_defaults_for_writes()
    from ..mobile import physical_binding as pb
    from ..mobile.physical_binding import PhysicalDeviceRequiredError
    # Device mode: refuse before CREATE if the connected phone identity is missing,
    # even when a live App Check token might still be present in-process.
    if (not activation.get("explicitly_disabled")
            and roglife.device_mode_enabled()):
        try:
            pb.require_campaign_physical_binding()
        except PhysicalDeviceRequiredError as exc:
            serial = activation.get("serial")
            detail = f" Causa: {exc.cause}."
            if isinstance(serial, str) and serial.strip():
                detail += f" Serial: {serial.strip()}."
            return (
                "Identidade física do celular conectado é obrigatória para o envio."
                + detail
            )
    # Status-only reads never mint; renew here so admission can clear an
    # expired/near-expiry cache before consulting historical refusals.
    status = appcheck.ensure_app_check_fresh()
    if status.get("token_available"):
        return None
    if activation.get("explicitly_disabled") or not roglife.device_mode_enabled():
        return _DEVICE_DISABLED
    if _has_journal_files():
        rejected = rejected_create_owners(accounts, upload.list_sidecars())
        if rejected:
            return _GUIDANCE + " Contas afetadas: " + ", ".join(sorted(rejected))
    diagnostic = status.get("diagnostic") or {}
    cause = diagnostic.get("category") or status.get("failure")
    message = diagnostic.get("message")
    detail = ""
    if isinstance(cause, str) and cause.strip():
        detail += f" Causa: {cause.strip()}."
    if isinstance(message, str) and message.strip():
        detail += f" {message.strip().rstrip('.')}."
    serial = activation.get("serial") or (status.get("device") or {}).get("serial")
    if isinstance(serial, str) and serial.strip():
        detail += f" Serial: {serial.strip()}."
    return _UNAVAILABLE + detail


def _has_journal_files() -> bool:
    """Read-only existence probe; an empty profile needs no migration or lease.

    A real receipt is still read by the authoritative, locked journal decoder.
    Only genuine absence is ignored; unreadable stores remain an admission error.
    """
    from .. import config

    for root in (config.DATA_DIR, config.MEDIA_DATA_DIR):
        try:
            if any(path.name.lower().endswith(".json") for path in (root / "sidecars").iterdir()):
                return True
        except FileNotFoundError:
            continue
    return False
