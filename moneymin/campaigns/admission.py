"""Candidate admission rules shared by previews and campaign execution."""
from __future__ import annotations


def audio_possible(clip: dict) -> bool:
    """Known absence blocks selection; unknown Ego4D audio is probed later."""
    if clip.get("source") == "nymeria" or str(clip.get("clip_uid") or "").startswith("nymeria:"):
        return False
    return clip.get("has_audio") is not False


def sent_emails(registry_key: str, clip: dict, sent_emails_for) -> set[str]:
    identities = [clip["clip_uid"], *clip.get("dedup_clip_uids", [])]
    return set().union(*(sent_emails_for(registry_key, uid) for uid in identities))


def reserved_emails(config, clip: dict) -> set[str]:
    identities = [clip["clip_uid"], *clip.get("dedup_clip_uids", [])]
    return set().union(*(set(config.recovery_exclusions.get(uid, [])) for uid in identities))


def footage_overlaps(previous: dict, candidate: dict, *, clip_window, same_action) -> bool:
    """Compare source footage across categories, variants and clock domains."""
    if str(previous.get("source") or "ego4d") != str(candidate.get("source") or "ego4d"):
        return False
    if previous.get("source") == "nymeria":
        def device_window(row: dict) -> tuple[int, int] | None:
            values = row.get("device_window_ns") or row.get("planned_device_window_ns")
            if (row.get("source_clock_domain") == "aria_DEVICE_TIME_ns"
                    and isinstance(values, (list, tuple)) and len(values) == 2
                    and all(type(value) is int and value >= 0 for value in values)
                    and values[1] > values[0]):
                return values[0], values[1]
            return None
        old_window, new_window = device_window(previous), device_window(candidate)
        if old_window and new_window:
            if previous.get("parent_video_uid") != candidate.get("parent_video_uid"):
                return False
            overlap = min(old_window[1], new_window[1]) - max(old_window[0], new_window[0])
            return overlap * 5 >= min(old_window[1] - old_window[0], new_window[1] - new_window[0]) * 3
        if previous.get("acquisition_required") is True or candidate.get("acquisition_required") is True:
            old_ids = {previous.get("clip_uid"), *previous.get("dedup_clip_uids", [])} - {None, ""}
            new_ids = {candidate.get("clip_uid"), *candidate.get("dedup_clip_uids", [])} - {None, ""}
            return bool(old_ids & new_ids)
    if (previous.get("parent_video_uid") and candidate.get("parent_video_uid")
            and clip_window(previous) is not None and clip_window(candidate) is not None):
        return same_action([previous], candidate)
    old_ids = {previous.get("clip_uid"), *previous.get("dedup_clip_uids", [])} - {None, ""}
    new_ids = {candidate.get("clip_uid"), *candidate.get("dedup_clip_uids", [])} - {None, ""}
    return bool(old_ids & new_ids)
