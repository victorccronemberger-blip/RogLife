"""Read-only, source-bound category review metadata for media providers."""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


_RESOURCE_PATH = Path(__file__).with_name("resources") / "provider_category_reviews.json"
_PROVIDERS = {"holoassist", "nymeria"}
_SHA1 = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_URL_OR_PATH = re.compile(
    r"(?:\b(?:https?|ftp|file)://|\bwww\.|(?:^|\s)[A-Za-z]:[\\/]|"
    r"(?:^|\s)\\\\[^\\\s]+\\[^\\\s]+|(?:^|\s)/(?:[^\s]+/)+[^\s]*)",
    re.IGNORECASE,
)
_EVIDENCE_FIELDS = ("method", "sampled_images", "duration_s", "description")


def _pending(reason: str | None = None) -> dict[str, Any]:
    result = {
        "status": "pending",
        "confirmed": False,
        "task_names": [],
        "suggested_task_names": [],
        "evidence_kind": "none",
        "asset_binding": "not_checked",
    }
    if reason is not None:
        result["reason"] = reason
    return result


@lru_cache(maxsize=8)
def _read_resource(
    path: str,
    mtime_ns: int,
    size: int,
    ctime_ns: int,
    inode: int,
) -> dict[str, Any] | None:
    """Cache only by current path/stat identity; callers never receive this dict."""
    del mtime_ns, size, ctime_ns, inode  # part of the cache key
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(value, dict)
        or type(value.get("schema")) is not int
        or value.get("schema") != 1
        or not isinstance(value.get("providers"), dict)
    ):
        return None
    return value


def _signature(path: Path) -> tuple[Any, ...]:
    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = str(path.absolute())
    try:
        stat = path.stat()
    except OSError:
        return (resolved, "missing")
    return (
        resolved,
        stat.st_mtime_ns,
        stat.st_size,
        stat.st_ctime_ns,
        getattr(stat, "st_ino", 0),
    )


def resource_signature() -> tuple[Any, ...]:
    """Return an immutable identity for the packaged review resource state."""
    return _signature(Path(_RESOURCE_PATH))


def _resource() -> dict[str, Any] | None:
    path = Path(_RESOURCE_PATH)
    signature = _signature(path)
    if len(signature) == 2 and signature[1] == "missing":
        return None
    try:
        return _read_resource(
            signature[0],
            signature[1],
            signature[2],
            signature[3],
            signature[4],
        )
    except (OSError, TypeError, ValueError):
        return None


def _safe_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip() or _URL_OR_PATH.search(value):
        return None
    return value


def _names(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    names = []
    for item in value:
        text = _safe_text(item)
        if text is None:
            return None
        names.append(text)
    if len(set(names)) != len(names):
        return None
    return names


def _evidence(value: Any, *, provider: str) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if provider == "nymeria":
        method = _safe_text(value.get("method"))
        description = _safe_text(value.get("description"))
        summary_rows = value.get("summary_rows")
        if (
            method is None
            or description is None
            or not isinstance(summary_rows, list)
            or not summary_rows
            or any(type(row) is not int or row < 1 for row in summary_rows)
            or len(set(summary_rows)) != len(summary_rows)
        ):
            return None
        return {"method": method, "description": description, "summary_rows": list(summary_rows)}
    if any(key not in value for key in _EVIDENCE_FIELDS):
        return None
    method = _safe_text(value.get("method"))
    description = _safe_text(value.get("description"))
    sampled = value.get("sampled_images")
    duration = value.get("duration_s")
    if method is None or description is None:
        return None
    if type(sampled) is int:
        if sampled <= 0:
            return None
        sampled_copy: int | list[str | int | float] = sampled
    elif isinstance(sampled, list):
        if not sampled:
            return None
        sampled_copy = []
        for item in sampled:
            if type(item) is str:
                safe_item = _safe_text(item)
                if safe_item is None:
                    return None
                sampled_copy.append(safe_item)
            elif type(item) is int:
                sampled_copy.append(item)
            elif type(item) is float and math.isfinite(item):
                sampled_copy.append(item)
            else:
                return None
    else:
        return None
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        return None
    # Construct a fresh object containing only the four approved fields.
    return {
        "method": method,
        "sampled_images": sampled_copy,
        "duration_s": duration,
        "description": description,
    }


def _validated_review(provider: str, raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    evidence = _evidence(raw.get("evidence"), provider=provider)
    if evidence is None:
        return None
    if provider == "holoassist":
        task_names = _names(raw.get("task_names"))
        annotation_digest = raw.get("annotation_sha256")
        video_digest = raw.get("source_video_sha256")
        video_bytes = raw.get("source_video_bytes")
        if (
            raw.get("status") != "confirmed"
            or raw.get("canonicalization", "json_sorted_ascii_v1") != "json_sorted_ascii_v1"
            or task_names is None
            or not isinstance(annotation_digest, str)
            or not _SHA256.fullmatch(annotation_digest)
            or not isinstance(video_digest, str)
            or not _SHA256.fullmatch(video_digest)
            or type(video_bytes) is not int
            or video_bytes <= 0
        ):
            return None
        return {
            "status": "confirmed",
            "annotation_digest": annotation_digest.lower(),
            "digest_kind": "sha256",
            "task_names": task_names,
            "suggested_task_names": [],
            "evidence_kind": "historical_category_review",
            "evidence": evidence,
            "source_video_sha256": video_digest.lower(),
            "source_video_bytes": video_bytes,
        }
    if provider == "nymeria":
        task_names = _names(raw.get("suggested_task_names"))
        annotation_digest = raw.get("annotation_sha1")
        if (
            raw.get("status") != "suggested_from_summary"
            or task_names is None
            or not isinstance(annotation_digest, str)
            or not _SHA1.fullmatch(annotation_digest)
        ):
            return None
        return {
            "status": "suggested_from_summary",
            "annotation_digest": annotation_digest.lower(),
            "digest_kind": "sha1",
            "task_names": [],
            "suggested_task_names": task_names,
            "evidence_kind": "summary_suggestion",
            "evidence": evidence,
        }
    return None


def _lookup(provider: str, source_id: str) -> dict[str, Any] | None:
    if not isinstance(provider, str) or provider not in _PROVIDERS or not isinstance(source_id, str) or not source_id:
        return None
    root = _resource()
    if root is None:
        return None
    providers = root["providers"]
    provider_reviews = providers.get(provider)
    if not isinstance(provider_reviews, dict):
        return None
    # Source IDs are exact keys. No hash-based identity fallback is permitted.
    return _validated_review(provider, provider_reviews.get(source_id))


def has_review(provider: str, source_id: str) -> bool:
    """Return whether this exact provider/source pair has a valid review record."""
    return _lookup(provider, source_id) is not None


def category_review(
    provider: str,
    source_id: str,
    *,
    annotation_digest: str | None = None,
    video_digest: str | None = None,
) -> dict[str, Any]:
    """Return independent, whitelisted review metadata bound to source digests."""
    review = _lookup(provider, source_id)
    if review is None:
        return _pending()

    if annotation_digest is None:
        return _pending("annotation_digest_required")
    if not isinstance(annotation_digest, str):
        return _pending("annotation_digest_mismatch")
    supplied_annotation_digest = annotation_digest.lower()
    expected_pattern = _SHA256 if review["digest_kind"] == "sha256" else _SHA1
    if (
        not expected_pattern.fullmatch(annotation_digest)
        or supplied_annotation_digest != review["annotation_digest"]
    ):
        return _pending("annotation_digest_mismatch")

    if review["status"] == "suggested_from_summary":
        evidence = dict(review["evidence"])
        if isinstance(evidence.get("sampled_images"), list):
            evidence["sampled_images"] = list(evidence["sampled_images"])
        if isinstance(evidence.get("summary_rows"), list):
            evidence["summary_rows"] = list(evidence["summary_rows"])
        return {
            "status": "suggested_from_summary",
            "confirmed": False,
            "task_names": [],
            "suggested_task_names": list(review["suggested_task_names"]),
            "evidence_kind": review["evidence_kind"],
            "asset_binding": "not_checked",
            "reason": None,
            "evidence": evidence,
        }

    asset_binding = "not_checked"
    status = "confirmed"
    confirmed = True
    reason = None
    task_names = list(review["task_names"])
    if video_digest is not None:
        if not isinstance(video_digest, str) or not _SHA256.fullmatch(video_digest) or video_digest.lower() != review["source_video_sha256"]:
            status = "stale"
            confirmed = False
            asset_binding = "mismatch"
            reason = "source_video_digest_mismatch"
            task_names = []
        else:
            asset_binding = "matched"
    return {
        "status": status,
        "confirmed": confirmed,
        "task_names": task_names,
        "suggested_task_names": [],
        "evidence_kind": review["evidence_kind"],
        "asset_binding": asset_binding,
        "reason": reason,
        "evidence": {
            **review["evidence"],
            "sampled_images": list(review["evidence"]["sampled_images"])
            if isinstance(review["evidence"]["sampled_images"], list)
            else review["evidence"]["sampled_images"],
        },
        "source_video_sha256": review["source_video_sha256"],
        "source_video_bytes": review["source_video_bytes"],
    }
