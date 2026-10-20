"""Compatibility facade for public Android USB diagnostics."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from ..mobile.android import diagnostics as _diagnostics
from ..mobile.android import discovery as _discovery


_DEADLINE_SECONDS = _diagnostics._DEADLINE_SECONDS
_WINDOWS = os.name == "nt"
_PROPERTIES = _diagnostics._PROPERTIES
_SERIAL = _diagnostics._SERIAL
_UID = _diagnostics._UID
_MESSAGES = _diagnostics._MESSAGES


def _find_adb() -> str | None:
    return _discovery.find_adb(
        windows=_WINDOWS,
        os_module=os,
        path_type=Path,
        shutil_module=shutil,
    )


def _text(value: str) -> str:
    return _diagnostics._text(value)


def _inventory(output: str) -> list[dict[str, str]]:
    return _diagnostics._inventory(output, text=_text)


def _connection_failure(output: str) -> str | None:
    return _diagnostics._connection_failure(output)


def snapshot(serial: str | None = None) -> dict:
    """Return a stable public snapshot while honoring legacy patch points."""
    return _diagnostics.snapshot(
        serial,
        find_adb=_find_adb,
        subprocess_module=subprocess,
        time_module=time,
        windows=_WINDOWS,
        datetime_type=datetime,
        timezone=timezone,
        deadline_seconds=_DEADLINE_SECONDS,
        properties=_PROPERTIES,
        serial_pattern=_SERIAL,
        uid_pattern=_UID,
        messages=_MESSAGES,
        text=_text,
        inventory=_inventory,
        connection_failure=_connection_failure,
    )