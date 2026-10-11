"""Durable per-installation journal storage and legacy migration."""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Callable


@dataclass(frozen=True)
class StorageServices:
    data_dir: Path
    media_data_dir: Path
    tokens_dir: Callable[[], Path]
    token_records: Callable[[Path], dict]
    load_json_state: Callable[..., Any]
    save_json: Callable[..., Any]
    recovery_read_error: type[Exception]
    lock: Any


def _copy_archive_without_overwrite(
    source: Path, destination: Path, services: StorageServices,
) -> None:
    payload = source.read_bytes()
    try:
        with destination.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if destination.read_bytes() != payload:
            raise services.recovery_read_error("archive_conflict", destination)


def _journal_matches(path: Path, expected: dict, services: StorageServices) -> bool:
    try:
        actual = services.load_json_state(path, None)
    except (ValueError, OSError):
        raise services.recovery_read_error("journal_unreadable", path) from None
    return (isinstance(actual, dict)
            and json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True))


def _save_migrated_journal_without_overwrite(
    target: Path, row: dict, services: StorageServices,
) -> None:
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.",
                                     suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        services.save_json(temporary, row)
        try:
            os.link(temporary, target)
        except FileExistsError:
            if not _journal_matches(target, row, services):
                raise services.recovery_read_error("journal_conflict", target)
    finally:
        temporary.unlink(missing_ok=True)


def journal_directory(services: StorageServices) -> Path:
    """Migrate journals owned by this installation without replacing data."""
    destination = services.data_dir / "sidecars"
    destination.mkdir(parents=True, exist_ok=True)
    legacy = services.media_data_dir / "sidecars"
    if legacy.resolve() == destination.resolve() or not legacy.is_dir():
        return destination
    with services.lock:
        owners = set(services.token_records(services.tokens_dir()))
        if not owners:
            return destination
        marker_path = services.data_dir / "sidecar_migration.json"
        try:
            marker = services.load_json_state(marker_path, {})
        except (ValueError, OSError):
            raise services.recovery_read_error("migration_marker") from None
        if not isinstance(marker, dict):
            raise services.recovery_read_error("migration_marker")
        source_key = str(legacy.resolve())
        migrated = marker.get(source_key, [])
        if not isinstance(migrated, list) or any(not isinstance(name, str) for name in migrated):
            raise services.recovery_read_error("migration_marker")
        imported = set(migrated)
        pending = []
        for path in legacy.glob("*.json"):
            if path.name in imported:
                continue
            try:
                row = services.load_json_state(path, {})
            except (ValueError, OSError):
                raise services.recovery_read_error("migration_source", path) from None
            if (not isinstance(row, dict)
                    or str(row.get("account_email", "")).strip().casefold() not in owners):
                continue
            sid = row.get("session_id")
            chunk = row.get("chunk_index", 0)
            if (not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", sid)
                    or type(chunk) is not int or chunk < 0
                    or path.name != f"{sid}{'__' + str(chunk) if chunk else ''}.json"):
                raise services.recovery_read_error("migration_source", path)
            target = destination / path.name
            archive = path.with_suffix(".data.zip")
            copied_archive = destination / archive.name
            if archive.exists():
                if archive.resolve().parent != legacy.resolve():
                    raise ValueError("Arquivo de retomada fora da biblioteca de origem.")
                row["sidecar_data_path"] = str(copied_archive.resolve())
            if target.exists() and not _journal_matches(target, row, services):
                raise services.recovery_read_error("journal_conflict", target)
            if (archive.exists() and copied_archive.exists()
                    and archive.read_bytes() != copied_archive.read_bytes()):
                raise services.recovery_read_error("archive_conflict", copied_archive)
            pending.append((path, row, target, archive, copied_archive))
        for path, row, target, archive, copied_archive in pending:
            if archive.exists():
                _copy_archive_without_overwrite(archive, copied_archive, services)
            _save_migrated_journal_without_overwrite(target, row, services)
            imported.add(path.name)
        if imported != set(migrated):
            marker[source_key] = sorted(imported)
            services.save_json(marker_path, marker)
    return destination


__all__ = ["StorageServices", "journal_directory"]
