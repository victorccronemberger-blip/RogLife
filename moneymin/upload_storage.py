"""Compatibility facade for the durable delivery journal store."""
from __future__ import annotations

# These names remain patchable for existing integrations and tests. The
# implementation lives in delivery.storage and receives this facade's current
# dependencies on each call.
import json
import os
import re
import tempfile
import threading
from pathlib import Path

from . import config, token_store
from .atomic_io import load_json_state, save_json
from .campaign_state import campaign_state_operation
from .delivery import storage as _storage
from .recovery_errors import RecoveryReadError

_LOCK = threading.RLock()


def _services() -> _storage.StorageServices:
    return _storage.StorageServices(
        data_dir=config.DATA_DIR,
        media_data_dir=config.MEDIA_DATA_DIR,
        tokens_dir=config.tokens_dir,
        token_records=token_store.records,
        load_json_state=load_json_state,
        save_json=save_json,
        recovery_read_error=RecoveryReadError,
        lock=_LOCK,
    )


@campaign_state_operation
def journal_directory() -> Path:
    return _storage.journal_directory(_services())


__all__ = ["journal_directory"]
