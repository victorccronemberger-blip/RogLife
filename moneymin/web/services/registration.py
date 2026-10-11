"""registration services facade preserving the server compatibility contract."""
from __future__ import annotations

REQUIRES = ('AuthError', 'JsonStateError', 'Session', '_BULK_REGISTER_LOCK', '_BULK_REGISTER_STATE', '_CROWTADO_SIGNUP_RETRIES', '_CROWTADO_SIGNUP_RETRY_DELAY_S', '_INTEGRATION_OPERATION_LOCK', '_PERSISTENCE_LOCK', '_REGISTRATION_BATCH_RESTORE_BLOCKED', '_REGISTRATION_BATCH_RESTORE_MESSAGE', '_STEP_LABELS', '_apply_hostinger', '_cache_org_key', '_full_register_account', '_hostinger_profiles', '_integration_error', '_migrate_legacy_integrations', '_register_account_steps', '_register_claru_steps', '_registration_failure', '_registration_gate', '_save_account_check', '_save_crowtado_cred', '_set_account_removed', '_step_fail', '_step_ok', '_step_skip', '_validate_minute_membership', 'account_bans', 'config', 'credential_store', 'hostinger_mail', 'math', 'org_policy', 'registration_limits', 'registration_state', 'save_secure_settings', 'time', 'token_store', 'unicodedata', 'uuid')


from .registration_domain.checkpoints import _save_registration_batch
from .registration_domain.checkpoints import _restore_registration_batch
from .registration_domain.checkpoints import _mark_bulk_account_removed
from .registration_domain.checkpoints import _step_ok
from .registration_domain.checkpoints import _step_skip
from .registration_domain.checkpoints import _step_fail
from .registration_domain.admission import _registration_failure
from .registration_domain.admission import _registration_gate
from .registration_domain.admission import _valid_registration_name
from .registration_domain.admission import _hostinger_is_configured
from .registration_domain.admission import _registration_domains
from .registration_domain.admission import _recover_hostinger_domains
from .registration_domain.admission import _preflight_checks
from .registration_domain.resume import _validate_minute_membership
from .registration_domain.resume import _full_register_account
from .registration_domain.resume import _full_register_claru_minute
from .registration_domain.resume import _register_claru_steps
from .registration_domain.resume import _register_account_steps
