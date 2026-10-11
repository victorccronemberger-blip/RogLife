"""
server.py — Servico local da interface Qt do QMoney (Flask).

Endpoints JSON consumidos exclusivamente pelo aplicativo desktop:

  Contas
    GET    /api/accounts                 lista contas (token_*.json em secrets/)
    POST   /api/accounts                 {email, password} -> login (adicionar)
    POST   /api/accounts/register        {email, password} -> cria conta no Minute
                                         (convite fixo), salva token + senha p/ Saldos
    DELETE /api/accounts/<email>         remove a conta (apaga o token)
    POST   /api/accounts/<email>/check   valida token + resolve org_key (cacheia)
    POST   /api/accounts/check-all       valida todas em paralelo e separa desativadas

  Categorias (tasks do Minute cruzadas com cenários Ego4D elegíveis)
    GET    /api/tasks?email=...          lista categorias elegíveis p/ campanha

  Preferências (data/webui_prefs.json)
    GET    /api/preferences
    PUT    /api/preferences              merge raso do body nas prefs

  Campanha (uma por vez — ver web.runner)
    POST   /api/campaigns                inicia; 400 body inválido; se ocupado,
                                         devolve a campanha existente (idempotente)
                                         body: {accounts, tasks, count,
                                         min_dur_s, max_dur_s,
                                         delay_mode (off|clip|fixed), delay_s,
                                         paralelismo automático entre contas,
                                         active_hours: [7, 18] | null}
    GET    /api/campaigns/current?since=N  estado + eventos novos (polling)
    POST   /api/campaigns/stop           parada cooperativa

  Acelerador (pré-cache retomável de HoloAssist ou Ego4D)
    GET    /api/holo-cache               cobertura local + estado do runner
                                         ?provider=holoassist|ego4d
    POST   /api/holo-cache/start         inicia download/normalização em 2º plano
    POST   /api/holo-cache/stop          para depois do clipe atual

  Armazenamento
    POST   /api/storage/cleanup           remove mídia/IMU baixada e derivados

  Histórico
    GET    /api/logs                     lista data/campaign_*.json (resumo)
    GET    /api/logs/<nome>              detalhe do log
    POST   /api/logs/<nome>/status       consulta status das sessões no backend

  Saldos (crowtado — cache em data/balances.json)
    GET    /api/balances                 saldos cacheados + estado do runner
    POST   /api/balances/refresh         {emails?} -> consulta em 2º plano (409 se ocupado)
    POST   /api/balances/payout-methods/apply-all -> configura método em todas as contas Crowtado
    POST   /api/balances/withdraw        {email} -> solicita saque pelo método preferido
    POST   /api/balances/withdraw-all    solicita saques para contas elegíveis
    PUT    /api/balances/credentials     {email, password} -> salva senha do crowtado
                                         (secrets/crowtado_passwords.json)

  Registro de enviados (data/sent_videos.json — dedup entre campanhas)
    GET    /api/sent                     resumo por cenário (clipes/envios)
    POST   /api/sent/reset               limpa o registro (body: {scenario?})
"""
from __future__ import annotations

import configparser
import datetime
from functools import wraps
import hashlib
import json
import math
import re
import os
import platform
import random
import shutil
import sqlite3
import sys
import threading
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from flask import Flask, g, jsonify, request

from .. import banned_store
from .. import (
    campaign, config, crowtado, ego4d, ego4d_library, ego_accelerator, fx, holo_accelerator, holoassist,
    hostinger_mail, identity, org_policy, readiness, sent_registry, credential_store, prepared_library, nymeria_library,
    task_catalog as library_task_catalog,
)
from ..atomic_io import JsonStateError, decode_json_state, load_json, load_json_state, save_json
from ..jsonl_history import decode_jsonl_history
from .. import account_transfer, account_bans, token_store
from ..campaign import AccountSpec, CampaignConfig, TaskSpec
from ..minute_api import AuthError, Session, login, validate_task_catalog
from ..secure_store import SecureStoreError, load_secure_settings, save_secure_settings
from .account_issues import account_issue, issue_text
from . import wallet, registration_state, account_health, registration_limits
from .catalog_loader import CatalogLoader
from .library_runner import LibraryPreparationRunner
from .org_migration import OrgMigrationRunner
from .banned_monitor import BannedMonitor
from .runner import (
    BALANCES_RUNNER, HOLO_CACHE_RUNNER, RUNNER, friendly_campaign_error, _public_event,
)

from .dependencies import DependencyBindings
from .routes import registry as route_registry
from .services import accounts as _service_accounts
from .services import campaigns as _service_campaigns
from .services import catalogs as _service_catalogs
from .services import history as _service_history
from .services import integrations as _service_integrations
from .services import preferences as _service_preferences
from .services import registration as _service_registration
from .services import wallet as _service_wallet
_global_bindings = DependencyBindings(globals())
_bindings_accounts = _global_bindings.scope(_service_accounts.REQUIRES)
_bindings_campaigns = _global_bindings.scope(_service_campaigns.REQUIRES)
_bindings_catalogs = _global_bindings.scope(_service_catalogs.REQUIRES)
_bindings_history = _global_bindings.scope(_service_history.REQUIRES)
_bindings_integrations = _global_bindings.scope(_service_integrations.REQUIRES)
_bindings_preferences = _global_bindings.scope(_service_preferences.REQUIRES)
_bindings_registration = _global_bindings.scope(_service_registration.REQUIRES)
_bindings_wallet = _global_bindings.scope(_service_wallet.REQUIRES)

PREFS_PATH = config.DATA_DIR / "webui_prefs.json"
BALANCES_PATH = config.DATA_DIR / "balances.json"
CROWTADO_PW_PATH = config.SECRETS_DIR / "crowtado_passwords.json"
MAX_DUR_S = 1800.0  # cap do recording-config do Minute
_PERSISTENCE_LOCK = threading.RLock()
_INTEGRATION_OPERATION_LOCK = threading.Lock()
_ACCOUNT_OPERATION_LOCK = threading.Lock()
ORG_MIGRATION = OrgMigrationRunner(config.DATA_DIR / "org_migration_report.json")
ACCOUNT_HEALTH_PATH = config.DATA_DIR / "account_health.json"
_BULK_REGISTER_LOCK = threading.Lock()
_BULK_REGISTER_STATE: dict[str, Any] = {"state": "idle"}
_REGISTRATION_BATCH_RESTORE_BLOCKED = False
_REGISTRATION_BATCH_RESTORE_MESSAGE = (
    "O histórico de cadastro não pôde ser confirmado. O arquivo original foi preservado; "
    "revise o progresso e as contas antes de liberar novas criações."
)
_HEAVY_RUNNER_LOCK = threading.Lock()
from .. import recovery, campaign_start_store
from .. import campaign_reset
from ..campaign_state import campaign_state_lease, CampaignStateLeaseError
from ..operation_lease import OperationLeaseError
RECOVERY = recovery.RecoveryRunner()
_WITHDRAW_LOCK = threading.Lock()
_PAYOUT_OPERATION_LOCK = threading.Lock()
_WALLET_COMMAND_LOCK = threading.Lock()

def _serialize_wallet_command(handler):
    return _service_wallet._serialize_wallet_command(_bindings_wallet, handler)
_WITHDRAW_IN_FLIGHT: set[str] = set()
_WITHDRAW_LAST_REQUEST: dict[str, float] = {}
_WITHDRAW_COOLDOWN_S = 60.0
_WITHDRAW_BULK_LOCK = threading.Lock()
_WITHDRAW_BULK_STATE: dict[str, Any] = {"state": "idle", "total": 0, "done": 0, "results": []}
_WITHDRAW_BULK_LOADED = False
_WITHDRAW_COOLDOWN_LOADED = False
_PAYOUT_METHOD_LOCK = threading.Lock()
_PAYOUT_METHOD_STATE: dict[str, Any] = {"state": "idle", "total": 0, "done": 0, "results": []}

def _payout_method_snapshot() -> dict[str, Any]:
    return _service_wallet._payout_method_snapshot(_bindings_wallet)

def _payout_method_run(creds: dict[str, str], method: str, legal_name: str, destination_email: str) -> None:
    return _service_wallet._payout_method_run(_bindings_wallet, creds, method, legal_name, destination_email)

def _withdraw_bulk_path() -> Path:
    return _service_wallet._withdraw_bulk_path(_bindings_wallet)

def _withdraw_cooldown_path() -> Path:
    return _service_wallet._withdraw_cooldown_path(_bindings_wallet)

def _wise_cleanup_path() -> Path:
    return _service_wallet._wise_cleanup_path(_bindings_wallet)

def _wise_cleanup_snapshot() -> dict[str, Any]:
    return _service_wallet._wise_cleanup_snapshot(_bindings_wallet)

def _finish_wise_cleanup(email: str, password: str) -> dict[str, Any]:
    return _service_wallet._finish_wise_cleanup(_bindings_wallet, email, password)

def _withdraw_wise_flow(email: str, password: str, wise: dict[str, str]) -> dict[str, Any]:
    return _service_wallet._withdraw_wise_flow(_bindings_wallet, email, password, wise)

def _load_withdraw_cooldowns_locked() -> None:
    return _service_wallet._load_withdraw_cooldowns_locked(_bindings_wallet)

def _load_withdraw_bulk_locked() -> None:
    return _service_wallet._load_withdraw_bulk_locked(_bindings_wallet)

def _save_withdraw_bulk_locked() -> None:
    return _service_wallet._save_withdraw_bulk_locked(_bindings_wallet)

def _wise_withdraw_options(body: dict[str, Any]) -> dict[str, str] | None:
    return _service_wallet._wise_withdraw_options(_bindings_wallet, body)

def _withdraw_once(email: str, password: str, wise: dict[str, str] | None=None) -> tuple[dict[str, Any], int]:
    return _service_wallet._withdraw_once(_bindings_wallet, email, password, wise)

def _withdraw_paypal_flow(email: str, password: str) -> dict[str, Any]:
    return _service_wallet._withdraw_paypal_flow(_bindings_wallet, email, password)

def _withdraw_once_locked(email: str, password: str, wise: dict[str, str] | None=None) -> tuple[dict[str, Any], int]:
    return _service_wallet._withdraw_once_locked(_bindings_wallet, email, password, wise)

def _withdraw_bulk_snapshot() -> dict[str, Any]:
    return _service_wallet._withdraw_bulk_snapshot(_bindings_wallet)

def _start_withdraw_worker(eligible, wise):
    return _service_wallet._start_withdraw_worker(_bindings_wallet, eligible, wise)

def _withdraw_bulk_run(creds: dict[str, str], wise: dict[str, str] | None=None) -> None:
    return _service_wallet._withdraw_bulk_run(_bindings_wallet, creds, wise)

def _balance_refresh_needed(accounts: list[dict[str, Any]], balances: dict[str, Any], connected: set[str], *, max_age_s: int=24 * 3600) -> list[str]:
    return _service_wallet._balance_refresh_needed(_bindings_wallet, accounts, balances, connected, max_age_s=max_age_s)

def _confirmed_available_balance(record: Any) -> bool:
    return _service_wallet._confirmed_available_balance(_bindings_wallet, record)

def _banned_withdraw_eligibility(row: dict[str, Any]) -> tuple[bool, str]:
    return _service_wallet._banned_withdraw_eligibility(_bindings_wallet, row)

def _remember_banned_withdraw_result(email: str, status: str, detail: str, *, balance: dict[str, Any] | None=None, balance_error: bool=False) -> None:
    return _service_wallet._remember_banned_withdraw_result(_bindings_wallet, email, status, detail, balance=balance, balance_error=balance_error)

_STEP_LABELS = {
    "proxy": "conexão do proxy",
    "ban_check": "verificar bloqueio na Crowtado",
    "crowtado_signup": "criação Crowtado",
    "save_partial": "proteger credencial local",
    "demographics": "demografia Crowtado",
    "minute_register": "registro Minute",
    "minute_identity": "confirmação do cadastro Minute",
    "link_minute": "vincular Minute na Crowtado",
    "validate": "validação pós-criação",
}
_STEP_ORDER = [
    "proxy", "ban_check", "save_partial", "crowtado_signup",
    "demographics", "minute_identity", "minute_register", "link_minute", "validate",
]
_CROWTADO_SIGNUP_RETRIES = 2
_CROWTADO_SIGNUP_RETRY_DELAY_S = 5.0

def _save_registration_batch() -> None:
    return _service_registration._save_registration_batch(_bindings_registration)

def _restore_registration_batch() -> None:
    return _service_registration._restore_registration_batch(_bindings_registration)

def _mark_bulk_account_removed(email: str) -> None:
    return _service_registration._mark_bulk_account_removed(_bindings_registration, email)

def _step_ok(detail: str='') -> dict[str, str]:
    return _service_registration._step_ok(_bindings_registration, detail)

def _step_skip(detail: str='') -> dict[str, str]:
    return _service_registration._step_skip(_bindings_registration, detail)

def _step_fail(detail: str='') -> dict[str, str]:
    return _service_registration._step_fail(_bindings_registration, detail)

def _registration_failure(exc, *, provider=None, detail=None):
    return _service_registration._registration_failure(_bindings_registration, exc, provider=provider, detail=detail)

def _registration_gate(*, claru=False):
    return _service_registration._registration_gate(_bindings_registration, claru=claru)

def _valid_registration_name(value: Any) -> bool:
    return _service_registration._valid_registration_name(_bindings_registration, value)

def _hostinger_is_configured() -> bool:
    return _service_registration._hostinger_is_configured(_bindings_registration)

def _registration_domains() -> list[dict[str, str]]:
    return _service_registration._registration_domains(_bindings_registration)

def _recover_hostinger_domains() -> list[str]:
    return _service_registration._recover_hostinger_domains(_bindings_registration)

def _preflight_checks(domain: str='') -> dict[str, Any]:
    return _service_registration._preflight_checks(_bindings_registration, domain)

def _validate_minute_membership(email: str) -> str:
    return _service_registration._validate_minute_membership(_bindings_registration, email)

def _full_register_account(email: str, password: str, identity_data: dict[str, Any], *, on_step: Any=None, resume: bool=False) -> dict[str, Any]:
    return _service_registration._full_register_account(_bindings_registration, email, password, identity_data, on_step=on_step, resume=resume)

def _full_register_claru_minute(row, *, on_step=None):
    return _service_registration._full_register_claru_minute(_bindings_registration, row, on_step=on_step)

def _register_claru_steps(email, password, identity_data, *, on_step=None):
    return _service_registration._register_claru_steps(_bindings_registration, email, password, identity_data, on_step=on_step)

def _register_account_steps(email: str, password: str, identity_data: dict[str, Any], *, on_step: Any=None, resume: bool=False) -> dict[str, Any]:
    return _service_registration._register_account_steps(_bindings_registration, email, password, identity_data, on_step=on_step, resume=resume)

def _tree_size(path: Path) -> tuple[int, int]:
    return _service_preferences._tree_size(_bindings_preferences, path)

def _storage_snapshot(*, include_path: bool=True) -> dict[str, Any]:
    return _service_preferences._storage_snapshot(_bindings_preferences, include_path=include_path)

# --- preferências ------------------------------------------------------------

def _finite_state_number(value: Any) -> bool:
    return _service_preferences._finite_state_number(_bindings_preferences, value)

def _invalid_local_state() -> None:
    return _service_preferences._invalid_local_state(_bindings_preferences)

def _validate_local_document(value: Any) -> None:
    return _service_preferences._validate_local_document(_bindings_preferences, value)

def _validate_local_prefs(value: Any) -> dict[str, Any]:
    return _service_preferences._validate_local_prefs(_bindings_preferences, value)

def _load_prefs() -> dict[str, Any]:
    return _service_preferences._load_prefs(_bindings_preferences)

def _save_prefs(prefs: dict[str, Any]) -> None:
    return _service_preferences._save_prefs(_bindings_preferences, prefs)

def _local_dataset_provider(value: Any) -> str:
    return _service_preferences._local_dataset_provider(_bindings_preferences, value)

def _cache_org_key(email: str, org_key: str) -> None:
    return _service_preferences._cache_org_key(_bindings_preferences, email, org_key)

def _removed_accounts_path() -> Path:
    return _service_preferences._removed_accounts_path(_bindings_preferences)

def _removed_accounts() -> set[str]:
    return _service_preferences._removed_accounts(_bindings_preferences)

def _set_account_removed(email: str, removed: bool) -> None:
    return _service_preferences._set_account_removed(_bindings_preferences, email, removed)

# --- integrações protegidas -------------------------------------------------

def _legacy_aws_credentials() -> dict[str, str]:
    return _service_integrations._legacy_aws_credentials(_bindings_integrations)

def _migrate_legacy_integrations() -> dict[str, Any]:
    return _service_integrations._migrate_legacy_integrations(_bindings_integrations)

def _apply_ego4d(values: dict[str, str]) -> None:
    return _service_integrations._apply_ego4d(_bindings_integrations, values)

def _hostinger_routes(value: Any) -> list[str]:
    return _service_integrations._hostinger_routes(_bindings_integrations, value)

def _hostinger_profiles(host: dict[str, Any]) -> list[dict[str, Any]]:
    return _service_integrations._hostinger_profiles(_bindings_integrations, host)

def _apply_hostinger(profiles: list[dict[str, Any]]) -> None:
    return _service_integrations._apply_hostinger(_bindings_integrations, profiles)

def _integration_error(exc: Exception, service: str) -> str:
    return _service_integrations._integration_error(_bindings_integrations, exc, service)

def _test_ego4d(values: dict[str, str]) -> dict[str, Any]:
    return _service_integrations._test_ego4d(_bindings_integrations, values)

def _integration_snapshot() -> dict[str, Any]:
    return _service_integrations._integration_snapshot(_bindings_integrations)

def _read_campaign_log(path: Path) -> dict[str, Any]:
    return _service_history._read_campaign_log(_bindings_history, path)

def _campaign_log_view(data: dict[str, Any], *, history_name: str | None=None) -> dict[str, Any]:
    return _service_history._campaign_log_view(_bindings_history, data, history_name=history_name)

# --- contas -------------------------------------------------------------------

def _load_account_health_history() -> dict[str, dict[str, Any]]:
    return _service_accounts._load_account_health_history(_bindings_accounts)

def _list_accounts() -> list[dict[str, Any]]:
    return _service_accounts._list_accounts(_bindings_accounts)

def _identity_route(email: str):
    return _service_accounts._identity_route(_bindings_accounts, email)

def _resolve_org(email: str, session: Session | None=None) -> str:
    return _service_accounts._resolve_org(_bindings_accounts, email, session)

def _check_minute_health(email: str) -> dict[str, Any]:
    return _service_accounts._check_minute_health(_bindings_accounts, email)

def _check_crowtado_health(email: str) -> dict[str, Any]:
    return _service_accounts._check_crowtado_health(_bindings_accounts, email)

def _check_account_health(email: str) -> dict[str, Any]:
    return _service_accounts._check_account_health(_bindings_accounts, email)

def _save_account_check(email: str, result: dict[str, Any]) -> None:
    return _service_accounts._save_account_check(_bindings_accounts, email, result)

def _migrate_account_org(email: str) -> dict[str, Any]:
    return _service_accounts._migrate_account_org(_bindings_accounts, email)

# --- saldos (crowtado) ----------------------------------------------------------

def _load_balances() -> dict[str, Any]:
    return _service_wallet._load_balances(_bindings_wallet)

def _save_balances(balances: dict[str, Any]) -> None:
    return _service_wallet._save_balances(_bindings_wallet, balances)

def _crowtado_creds(*, include_individual: bool=True) -> dict[str, str]:
    return _service_wallet._crowtado_creds(_bindings_wallet, include_individual=include_individual)

def _saved_account_password(email: str, legacy: dict[str, str] | None=None) -> str | None:
    return _service_wallet._saved_account_password(_bindings_wallet, email, legacy)

def _configured_crowtado_creds() -> dict[str, str]:
    return _service_wallet._configured_crowtado_creds(_bindings_wallet)

def _save_crowtado_cred(email: str, password: str) -> None:
    return _service_wallet._save_crowtado_cred(_bindings_wallet, email, password)

def _remove_account_data(email: str) -> None:
    return _service_accounts._remove_account_data(_bindings_accounts, email)

def _ban_accounts(issues: list[dict]) -> None:
    return _service_accounts._ban_accounts(_bindings_accounts, issues)

def _reconcile_account_bans() -> list[str]:
    return _service_accounts._reconcile_account_bans(_bindings_accounts)

def _archive_checked_ban(email: str, result: dict) -> None:
    return _service_accounts._archive_checked_ban(_bindings_accounts, email, result)

def _catalog_account_failure(email: str, error: Exception) -> tuple[dict, int]:
    return _service_catalogs._catalog_account_failure(_bindings_catalogs, email, error)

def _catalog_ban_record_confirms_exclusion(email: str) -> bool:
    return _service_catalogs._catalog_ban_record_confirms_exclusion(_bindings_catalogs, email)

def _preflight_fingerprint(emails: list[str], *, _include_cached_org: bool=True) -> str:
    return _service_catalogs._preflight_fingerprint(_bindings_catalogs, emails, _include_cached_org=_include_cached_org)

def _catalog_identity_fingerprint(emails: list[str]) -> str:
    return _service_catalogs._catalog_identity_fingerprint(_bindings_catalogs, emails)

def _invalidate_balance_after_withdrawal(email: str, result: dict[str, Any]) -> None:
    return _service_wallet._invalidate_balance_after_withdrawal(_bindings_wallet, email, result)

def _on_balance_result(email: str, summary: dict | None, erro: str | Exception | None) -> None:
    return _service_wallet._on_balance_result(_bindings_wallet, email, summary, erro)

def _on_claru_balance_result(email, summary, error):
    return _service_wallet._on_claru_balance_result(_bindings_wallet, email, summary, error)

def _balance_accounts():
    return _service_wallet._balance_accounts(_bindings_wallet)

def _last_withdrawal_receipt(configured: set[str]) -> dict[str, Any]:
    return _service_wallet._last_withdrawal_receipt(_bindings_wallet, configured)

def _withdraw_message(email: str, result: dict[str, Any]) -> str:
    return _service_wallet._withdraw_message(_bindings_wallet, email, result)

# --- app -----------------------------------------------------------------------

def _on_campaign_restriction(email: str) -> None:
    _service_accounts.on_campaign_restriction(_bindings_accounts, email)


# The runner is process-owned. App factories do not overwrite its callback;
# the account collaborator is resolved at invocation time for compatibility.
RUNNER.on_restriction = _on_campaign_restriction


def _validate_campaign_request(body: Any) -> None:
    return _service_campaigns._validate_campaign_request(_bindings_campaigns, body)

def _parse_duration_range(values) -> tuple[float, float]:
    return _service_campaigns._parse_duration_range(_bindings_campaigns, values)

def _require_local_api_token(*, for_testing: bool = False) -> str:
    """Require a desktop session; unauthenticated clients are explicit test fixtures."""
    if type(for_testing) is not bool:
        raise TypeError("for_testing deve ser booleano.")
    local_api_token = os.environ.get("QMONEY_LOCAL_API_TOKEN", "")
    if not local_api_token and (not for_testing or getattr(sys, "frozen", False)):
        raise RuntimeError("Inicie o serviço pela interface QMoney para proteger o acesso local.")
    return local_api_token

def create_app(*, for_testing: bool = False) -> Flask:
    import hmac
    local_api_token = _require_local_api_token(for_testing=for_testing)
    if not for_testing:
        _restore_registration_batch()
    preflights: dict[str, dict] = {}
    preflight_lock = threading.RLock()
    # Original captures use their own bounded, server-owned receipts. They
    # cannot fall back to a dataset plan or an unreviewed start.
    original_preflights: dict[str, dict] = {}
    original_starts: dict[str, dict] = {}
    # Per-service close barrier: a request admitted before closing may still
    # finish its preflight, but must never start another sending worker.
    campaign_drain = {"requested": False, "requests": 0}
    banned_monitor = BannedMonitor()
    # No QMoney o Flask e apenas o servico local consumido pela interface Qt.
    # Nenhum frontend web e publicado ou usado como fallback.
    app = Flask(__name__, static_folder=None)
    app.config["QMONEY_LOCAL_API_AUTHENTICATED"] = bool(local_api_token)
    task_catalog = CatalogLoader(timeout_s=1800)
    campaign_verifications = CatalogLoader(ttl_s=30, max_pending=1, timeout_s=900,
        timeout_message="A verificação demorou mais que o esperado. Nenhum envio foi iniciado. "
                        "Confira a conexão e tente verificar novamente em instantes.")
    accelerator_catalog = CatalogLoader()
    recovery_catalog = CatalogLoader(ttl_s=2)
    original_library_index = CatalogLoader(max_pending=1, timeout_s=180)
    prepared_library_inventory = CatalogLoader(ttl_s=300, max_pending=1, timeout_s=7200,
        timeout_message="A conferência dos arquivos locais demorou demais. Tente verificar o acervo novamente.")
    local_media_inventory = CatalogLoader(ttl_s=3, max_pending=1, timeout_s=300)
    nymeria_catalog = CatalogLoader(ttl_s=3, max_pending=1, timeout_s=900)
    ego4d_catalog = CatalogLoader(ttl_s=30, max_pending=1, timeout_s=900)
    nymeria_library_worker = LibraryPreparationRunner()
    app.extensions["nymeria_library_worker"] = nymeria_library_worker
    local_shutdown = {"requested": False}
    portable_setup = (nymeria_library.initialize_portable_catalog(os.environ.get("QMONEY_PORTABLE_ROOT"))
                      if not for_testing else {"state": "not_supplied"})

    # -- contas ---------------------------------------------------------------

    # -- categorias --------------------------------------------------------------

    # -- preferências -------------------------------------------------------------

    # -- prontidão, biblioteca e diagnóstico -----------------------------------

    # -- acelerador (HoloAssist ou Ego4D) --------------------------------------

    # -- campanha ---------------------------------------------------------------
    OriginalAdmissionError = route_registry.campaign_original.OriginalAdmissionError

    # -- histórico ----------------------------------------------------------------

    # -- limpeza revisada de e-mail (apenas INBOX -> lixeira) -------------------

    # -- saldos (crowtado) -----------------------------------------------------

    # -- registro de enviados ---------------------------------------------------

    dependencies = DependencyBindings(globals(), dict(locals()))
    app.extensions['feature_dependencies'] = dependencies
    route_registry.register_routes(app, dependencies)
    # Startup work is admitted only after composition succeeds.
    if portable_setup["state"] == "imported":
        # Only tiny official metadata/narration assets are synchronized here.
        # The existing worker reports progress and holds the campaign barrier;
        # the threaded HTTP service remains free to load accounts and health.
        with _HEAVY_RUNNER_LOCK:
            nymeria_library_worker.start("sync", lambda progress, stopped:
                nymeria_library.sync_catalog(progress=progress, should_stop=stopped),
                root=str(nymeria_library.data_root()))
    return app

def _log_path(name: str) -> Path | None:
    return _service_history._log_path(_bindings_history, name)

def _parse_active_hours(raw) -> tuple[int, int] | None | bool:
    return _service_campaigns._parse_active_hours(_bindings_campaigns, raw)

def _safe_org(email: str) -> str | None:
    return _service_accounts._safe_org(_bindings_accounts, email)

__all__ = ["create_app"]
