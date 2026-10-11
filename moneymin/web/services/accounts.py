"""accounts services facade preserving the server compatibility contract."""
from __future__ import annotations

REQUIRES = ('ACCOUNT_HEALTH_PATH', 'AuthError', 'CROWTADO_PW_PATH', 'Session', '_BULK_REGISTER_LOCK', '_BULK_REGISTER_STATE', '_PERSISTENCE_LOCK', '_ban_accounts', '_cache_org_key', '_check_crowtado_health', '_check_minute_health', '_crowtado_creds', '_identity_route', '_invalid_local_state', '_load_account_health_history', '_load_balances', '_load_prefs', '_mark_bulk_account_removed', '_remove_account_data', '_removed_accounts', '_resolve_org', '_save_account_check', '_save_balances', '_save_prefs', '_save_registration_batch', '_saved_account_password', '_set_account_removed', 'account_bans', 'account_health', 'account_issue', 'account_transfer', 'banned_store', 'campaign_state_lease', 'config', 'credential_store', 'crowtado', 'datetime', 'decode_json_state', 'issue_text', 'json', 'load_json_state', 'org_policy', 'registration_state', 'save_json', 'sys', 'time', 'token_store')


from .accounts_domain.persistence import _load_account_health_history
from .accounts_domain.persistence import _list_accounts
from .accounts_domain.persistence import _remove_account_data
from .accounts_domain.health import _identity_route
from .accounts_domain.health import _resolve_org
from .accounts_domain.health import _check_minute_health
from .accounts_domain.health import _check_crowtado_health
from .accounts_domain.health import _check_account_health
from .accounts_domain.health import _save_account_check
from .accounts_domain.health import _safe_org
from .accounts_domain.restrictions import _ban_accounts
from .accounts_domain.restrictions import on_campaign_restriction
from .accounts_domain.restrictions import _reconcile_account_bans
from .accounts_domain.restrictions import _archive_checked_ban
from .accounts_domain.organization import _migrate_account_org
