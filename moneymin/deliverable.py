"""Compatibility facade for local framed-video delivery packages."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import config, datasets, framing
from .delivery import package as _package


def _probe_video(video_path: str | Path) -> dict[str, Any]:
    from .sidecar import probe_video
    return probe_video(video_path)


def _services() -> _package.PackageServices:
    return _package.PackageServices(
        media_data_dir=config.MEDIA_DATA_DIR,
        dataset_catalog=datasets.CATALOG,
        save_manifest=framing.save_manifest,
        probe_video=_probe_video,
    )


def probe_duration_s(video_path: str | Path) -> float | None:
    return _package.probe_duration_s(video_path, _services())


def build_dossier_text(
    record: framing.FramingRecord,
    task: dict[str, Any],
    dataset: str,
    org_name: str = "",
    actions: list[tuple[str, str, str, str]] | None = None,
    video_size: int | None = None,
    duration_s: float | None = None,
    license_note: str = "",
) -> str:
    return _package.build_dossier_text(
        record, task, dataset, org_name=org_name, actions=actions,
        video_size=video_size, duration_s=duration_s, license_note=license_note,
        services=_services(),
    )


def build_package(
    video_path: str | Path,
    record: framing.FramingRecord,
    task: dict[str, Any],
    dataset: str,
    org_name: str = "",
    actions: list[tuple[str, str, str, str]] | None = None,
    license_note: str = "",
    out_root: Path | None = None,
) -> Path:
    return _package.build_package(
        video_path, record, task, dataset, org_name=org_name, actions=actions,
        license_note=license_note, out_root=out_root, services=_services(),
    )


__all__ = ["probe_duration_s", "build_dossier_text", "build_package"]
