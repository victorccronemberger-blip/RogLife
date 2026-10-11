"""registration routes facade preserving the server compatibility contract."""
from __future__ import annotations

REQUIRES = ('BALANCES_RUNNER', 'JsonStateError', 'RECOVERY', 'RUNNER', '_BULK_REGISTER_LOCK', '_BULK_REGISTER_STATE', '_REGISTRATION_BATCH_RESTORE_BLOCKED', '_REGISTRATION_BATCH_RESTORE_MESSAGE', '_STEP_LABELS', '_full_register_account', '_full_register_claru_minute', '_hostinger_is_configured', '_list_accounts', '_preflight_checks', '_recover_hostinger_domains', '_registration_domains', '_registration_gate', '_removed_accounts', '_save_registration_batch', '_saved_account_password', '_valid_registration_name', 'account_health', 'bulk_register_accounts', 'config', 'credential_store', 'crowtado', 'datetime', 'hashlib', 'identity', 'json', 'jsonify', 'org_policy', 'registration_limits', 'registration_state', 'request', 'threading')


from .registration_domain.account import register_account
from .registration_domain.account import list_claru_accounts
from .registration_domain.account import connect_claru_account
from .registration_domain.proxies import list_registration_proxies
from .registration_domain.proxies import import_registration_proxies
from .registration_domain.domains import list_account_domains
from .registration_domain.domains import bulk_register_preflight
from .registration_domain.bulk import bulk_register_status
from .registration_domain.bulk import stop_bulk_register
from .registration_domain.bulk import resume_account
from .registration_domain.bulk import bulk_register_accounts
