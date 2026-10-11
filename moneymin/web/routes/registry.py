"""Composition-only registry. Feature implementations live in their domain modules."""
from __future__ import annotations

from . import accelerator
from . import accounts
from . import campaign_admission
from . import campaign_execution
from . import campaign_original
from . import catalogs
from . import provider_catalogs
from . import history
from . import integrations
from . import library_ego4d
from . import library_nymeria
from . import mail
from . import preferences
from . import recovery
from . import registration
from . import runtime
from . import storage
from . import wallet

FEATURES = {
    'accelerator': accelerator,
    'accounts': accounts,
    'campaign_admission': campaign_admission,
    'campaign_execution': campaign_execution,
    'campaign_original': campaign_original,
    'catalogs': catalogs,
    'provider_catalogs': provider_catalogs,
    'history': history,
    'integrations': integrations,
    'library_ego4d': library_ego4d,
    'library_nymeria': library_nymeria,
    'mail': mail,
    'preferences': preferences,
    'recovery': recovery,
    'registration': registration,
    'runtime': runtime,
    'storage': storage,
    'wallet': wallet,
}

HANDLERS = (
    ('runtime', 'authenticate_local_client', (('before_request', 'handler', None),)),
    ('runtime', 'guard_local_shutdown', (('before_request', 'handler', None),)),
    ('runtime', 'shutdown_local_edition', (('post', 'literal', '/api/local/shutdown'),)),
    ('runtime', 'campaign_closing_response', ()),
    ('runtime', 'admit_campaign_request', (('before_request', 'handler', None),)),
    ('runtime', 'guard_campaign_state', (('before_request', 'handler', None),)),
    ('runtime', 'release_campaign_state', (('teardown_request', 'handler', None),)),
    ('runtime', 'release_campaign_request', (('teardown_request', 'handler', None),)),
    ('runtime', 'invalid_secure_store', (('errorhandler', 'dependency', 'SecureStoreError'),)),
    ('runtime', 'invalid_banned_store', (('errorhandler', 'dependency', 'banned_store.BannedStoreError'),)),
    ('runtime', 'invalid_token_identity', (('errorhandler', 'dependency', 'token_store.TokenStoreError'),)),
    ('runtime', 'invalid_authoritative_state', (('errorhandler', 'dependency', 'JsonStateError'),)),
    ('runtime', 'bound_original_request', (('before_request', 'handler', None),)),
    ('runtime', 'validate_json_body', (('before_request', 'handler', None),)),
    ('runtime', 'guard_account_operations', (('before_request', 'handler', None),)),
    ('runtime', 'release_account_operation', (('teardown_request', 'handler', None),)),
    ('runtime', 'index', (('get', 'literal', '/'),)),
    ('accounts', 'get_accounts', (('get', 'literal', '/api/accounts'),)),
    ('accounts', 'account_password', (('post', 'literal', '/api/accounts/password'),)),
    ('accounts', 'get_org_migration', (('get', 'literal', '/api/accounts/migration'),)),
    ('accounts', 'start_org_migration', (('post', 'literal', '/api/accounts/migration'),)),
    ('accounts', 'banned_monitor_snapshot', (('get', 'literal', '/api/accounts/banned/monitor'),)),
    ('accounts', 'banned_account_password', (('post', 'literal', '/api/accounts/banned/password'),)),
    ('accounts', 'request_banned_withdraw', (('post', 'literal', '/api/accounts/banned/withdraw'),)),
    ('accounts', 'refresh_banned_monitor', (('post', 'literal', '/api/accounts/banned/refresh'),)),
    ('accounts', 'banned_accounts', (('get', 'literal', '/api/accounts/banned'),)),
    ('accounts', 'import_accounts', (('post', 'literal', '/api/accounts/import'),)),
    ('accounts', 'export_accounts', (('post', 'literal', '/api/accounts/export'),)),
    ('accounts', 'add_account', (('post', 'literal', '/api/accounts'),)),
    ('registration', 'register_account', (('post', 'literal', '/api/accounts/register'),)),
    ('registration', 'list_claru_accounts', (('get', 'literal', '/api/accounts/claru'),)),
    ('registration', 'connect_claru_account', (('post', 'literal', '/api/accounts/claru/connect'),)),
    ('registration', 'list_registration_proxies', (('get', 'literal', '/api/accounts/proxies'),)),
    ('registration', 'import_registration_proxies', (('post', 'literal', '/api/accounts/proxies/import'),)),
    ('registration', 'list_account_domains', (('get', 'literal', '/api/accounts/domains'),)),
    ('registration', 'bulk_register_preflight', (('get', 'literal', '/api/accounts/bulk-register/preflight'),)),
    ('registration', 'bulk_register_status', (('get', 'literal', '/api/accounts/bulk-register/status'),)),
    ('registration', 'stop_bulk_register', (('post', 'literal', '/api/accounts/bulk-register/stop'),)),
    ('registration', 'resume_account', (('post', 'literal', '/api/accounts/<email>/resume'),)),
    ('registration', 'bulk_register_accounts', (('post', 'literal', '/api/accounts/bulk-register'),)),
    ('accounts', 'remove_account', (('delete', 'literal', '/api/accounts/<email>'),)),
    ('accounts', 'check_account', (('post', 'literal', '/api/accounts/<email>/check'),)),
    ('accounts', 'check_all_accounts', (('post', 'literal', '/api/accounts/check-all'),)),
    ('catalogs', 'get_tasks', (('get', 'literal', '/api/tasks'),)),
    ('preferences', 'get_prefs', (('get', 'literal', '/api/preferences'),)),
    ('preferences', 'put_prefs', (('put', 'literal', '/api/preferences'),)),
    ('integrations', 'get_integrations', (('get', 'literal', '/api/integrations'),)),
    ('integrations', 'put_ego4d_integration', (('put', 'literal', '/api/integrations/ego4d'),)),
    ('integrations', 'test_ego4d_integration', (('post', 'literal', '/api/integrations/ego4d/test'),)),
    ('integrations', 'prepare_ego4d_catalog', (('post', 'literal', '/api/integrations/ego4d/catalog'),)),
    ('integrations', 'put_hostinger_integration', (('put', 'literal', '/api/integrations/hostinger'),)),
    ('integrations', 'delete_hostinger_integration', (('delete', 'literal', '/api/integrations/hostinger/<profile_id>'),)),
    ('integrations', 'test_hostinger_integration', (('post', 'literal', '/api/integrations/hostinger/test'),)),
    ('storage', 'get_readiness', (('get', 'literal', '/api/readiness'),)),
    ('storage', 'get_storage_library', (('get', 'literal', '/api/storage/library'),)),
    ('storage', 'browse_local_media_library', (('get', 'literal', '/api/storage/library/items'),)),
    ('campaign_original', 'original_library_paths', ()),
    ('library_ego4d', 'browse_prepared_library', (('get', 'literal', '/api/library/ego4d/prepared'),)),
    ('library_ego4d', 'get_original_library_summary', (('get', 'literal', '/api/library/ego4d'),)),
    ('library_ego4d', 'index_original_library', (('post', 'literal', '/api/library/ego4d/index'),)),
    ('library_ego4d', 'browse_original_library', (('get', 'literal', '/api/library/ego4d/videos'),)),
    ('library_nymeria', 'get_nymeria_library', (('get', 'literal', '/api/library/nymeria'),)),
    ('library_nymeria', 'browse_nymeria_library', (('get', 'literal', '/api/library/nymeria/sequences'),)),
    ('library_nymeria', 'nymeria_library_operation', (('get', 'literal', '/api/library/nymeria/operation'),)),
    ('library_nymeria', 'stop_nymeria_library_operation', (('post', 'literal', '/api/library/nymeria/stop'),)),
    ('library_nymeria', 'start_nymeria_library_work', ()),
    ('library_nymeria', 'import_nymeria_library_manifest', (('post', 'literal', '/api/library/nymeria/import'),)),
    ('library_nymeria', 'sync_nymeria_library_catalog', (('post', 'literal', '/api/library/nymeria/sync'),)),
    ('library_nymeria', 'plan_nymeria_library_expansion', (('post', 'literal', '/api/library/nymeria/plan'),)),
    ('library_nymeria', 'acquire_nymeria_library_sequences', (('post', 'literal', '/api/library/nymeria/download'),)),
    ('runtime', 'get_health', (('get', 'literal', '/api/health'),)),
    ('runtime', 'get_runtime_readiness', (('get', 'literal', '/api/runtime'),)),
    ('runtime', 'get_diagnostics', (('get', 'literal', '/api/diagnostics'),)),
    ('accelerator', '_accelerator_provider', ()),
    ('accelerator', '_accelerator_tasks', ()),
    ('accelerator', '_accelerator_module', ()),
    ('accelerator', '_accelerator_budget_gb', ()),
    ('accelerator', '_accelerator_durations', ()),
    ('accelerator', 'holo_cache_status', (('get', 'literal', '/api/holo-cache'),)),
    ('accelerator', 'holo_cache_start', (('post', 'literal', '/api/holo-cache/start'),)),
    ('accelerator', 'holo_cache_stop', (('post', 'literal', '/api/holo-cache/stop'),)),
    ('accelerator', 'storage_cleanup', (('post', 'literal', '/api/storage/cleanup'),)),
    ('campaign_original', 'original_error', ()),
    ('campaign_execution', 'campaign_start_status', (('get', 'literal', '/api/campaigns/starts/<start_id>'),)),
    ('campaign_original', 'original_body', ()),
    ('campaign_original', 'original_pairs', ()),
    ('campaign_original', 'original_local_inspection', ()),
    ('campaign_original', 'original_session', ()),
    ('campaign_original', 'original_tasks', ()),
    ('campaign_original', 'original_task', ()),
    ('campaign_original', 'original_policy', ()),
    ('campaign_original', 'original_prepare', ()),
    ('campaign_original', 'original_summary', ()),
    ('campaign_original', 'original_not_busy', ()),
    ('campaign_original', 'original_receipt_current', ()),
    ('campaign_original', 'original_verify', ()),
    ('campaign_original', 'original_campaign_tasks', (('get', 'literal', '/api/campaigns/original/tasks'),)),
    ('campaign_original', 'original_campaign_preflight', (('post', 'literal', '/api/campaigns/original/preflight'),)),
    ('campaign_original', 'start_original_campaign', (('post', 'literal', '/api/campaigns/original'),)),
    ('campaign_admission', 'campaign_preflight', (('post', 'literal', '/api/campaigns/preflight'),)),
    ('campaign_admission', 'background_campaign_preflight', ()),
    ('campaign_admission', 'run_campaign_preflight', ()),
    ('campaign_admission', 'start_campaign', (('post', 'literal', '/api/campaigns'),)),
    ('campaign_execution', 'campaign_current', (('get', 'literal', '/api/campaigns/current'),)),
    ('campaign_execution', 'pause_campaign', (('post', 'literal', '/api/campaigns/pause'),)),
    ('recovery', 'recovery_snapshot', (('get', 'literal', '/api/recovery'),)),
    ('recovery', 'reconcile_recovery', (('post', 'literal', '/api/recovery/reconcile'),)),
    ('recovery', 'resume_recovery', (('post', 'literal', '/api/recovery/resume'),)),
    ('campaign_execution', 'resume_campaign', (('post', 'literal', '/api/campaigns/resume'),)),
    ('campaign_execution', 'campaign_stop', (('post', 'literal', '/api/campaigns/stop'),)),
    ('campaign_execution', 'campaign_close_drain', (('post', 'literal', '/api/campaigns/drain'),)),
    ('history', 'get_logs', (('get', 'literal', '/api/logs'),)),
    ('history', 'get_log', (('get', 'literal', '/api/logs/<name>'),)),
    ('history', 'get_log_status', (('post', 'literal', '/api/logs/<name>/status'),)),
    ('mail', 'mail_cleanup_status', (('get', 'literal', '/api/mail-cleanup'),)),
    ('mail', 'mail_cleanup_preview', (('post', 'literal', '/api/mail-cleanup/preview'),)),
    ('mail', 'mail_cleanup_apply', (('post', 'literal', '/api/mail-cleanup/apply'),)),
    ('mail', 'mail_cleanup_stop', (('post', 'literal', '/api/mail-cleanup/stop'),)),
    ('wallet', 'get_balances', (('get', 'literal', '/api/balances'),)),
    ('wallet', 'finish_pending_wise_cleanup', (('post', 'literal', '/api/balances/wise-cleanup'), ('custom', 'dependency', '_serialize_wallet_command'))),
    ('wallet', 'apply_all_payout_methods', (('post', 'literal', '/api/balances/payout-methods/apply-all'), ('custom', 'dependency', '_serialize_wallet_command'))),
    ('wallet', 'refresh_balances', (('post', 'literal', '/api/balances/refresh'), ('custom', 'dependency', '_serialize_wallet_command'))),
    ('wallet', 'stop_balance_refresh', (('post', 'literal', '/api/balances/stop'),)),
    ('wallet', 'request_balance_withdraw', (('post', 'literal', '/api/balances/withdraw'), ('custom', 'dependency', '_serialize_wallet_command'))),
    ('wallet', 'request_all_balance_withdrawals', (('post', 'literal', '/api/balances/withdraw-all'), ('custom', 'dependency', '_serialize_wallet_command'))),
    ('wallet', 'put_balance_credentials', (('put', 'literal', '/api/balances/credentials'), ('custom', 'dependency', '_serialize_wallet_command'))),
    ('history', 'get_sent', (('get', 'literal', '/api/sent'),)),
    ('history', 'reset_sent', (('post', 'literal', '/api/sent/reset'),)),
    ('campaign_execution', 'reset_campaign_state', (('post', 'literal', '/api/campaign/reset'),)),
    ('provider_catalogs', 'get_provider_catalog', (('get', 'literal', '/api/catalogs/providers'),)),
    ('provider_catalogs', 'prepare_provider_catalog', (('post', 'literal', '/api/catalogs/providers/prepare'),)),
)

def _dependency(name, dependencies):
    parts = name.split(".")
    value = getattr(dependencies, parts[0])
    for part in parts[1:]:
        value = getattr(value, part)
    return value


def register_routes(app, dependencies):
    """Bind every closure dependency before installing hooks/routes in original order."""
    for domain, name, decorators in HANDLERS:
        module = FEATURES[domain]
        dependencies.provide(name, dependencies.bind(getattr(module, name), module.REQUIRES))
    for domain, name, decorators in HANDLERS:
        handler = getattr(dependencies, name)
        for method, kind, argument in reversed(decorators):
            if method == "custom":
                handler = _dependency(argument, dependencies)(handler)
            elif kind == "handler":
                handler = getattr(app, method)(handler)
            else:
                value = argument if kind == "literal" else _dependency(argument, dependencies)
                handler = getattr(app, method)(value)(handler)
        dependencies.provide(name, handler)
