"""Ego4D preparation planning and normalization, independent of disk I/O."""
from __future__ import annotations


def ego_prepare_plan(clip: dict, clip_window_s) -> dict:
    clip_uid = clip["exported_clip_uid"]
    orig_start, orig_end = clip_window_s(clip)
    start_s, end_s = orig_start, orig_end
    parent_uid = str(clip.get("parent_video_uid") or clip_uid)
    needs_cut = bool(clip.get("needs_cut"))
    if needs_cut:
        media_uid = str(clip.get("media_uid") or parent_uid)
        media_offset = float(clip.get("media_time_offset_s") or 0.0)
        source_name = f"{media_uid}.mp4"
        norm_start = start_s - media_offset
    else:
        source_name = f"{clip_uid}.mp4"
        rel = start_s - orig_start
        norm_start = rel if rel > 0.02 else None
    return {
        "clip_uid": clip_uid, "parent_uid": parent_uid,
        "start_s": start_s, "end_s": end_s, "dur_s": end_s - start_s,
        "window_s": (start_s, end_s), "needs_cut": needs_cut,
        "source_name": source_name, "norm_start": norm_start,
        "imu_name": f"{parent_uid}_imu.csv", "native_name": f"{clip_uid}_native.mp4",
    }


def normalize_ego_clip(clip: dict) -> dict:
    out = dict(clip)
    uid = str(out.get("exported_clip_uid") or out.get("clip_uid") or "").strip()
    if not uid:
        raise RuntimeError("Clipe Ego4D sem clip_uid/exported_clip_uid")
    out["clip_uid"] = str(out.get("clip_uid") or uid)
    out["exported_clip_uid"] = uid
    window = out.get("window_s")
    if (out.get("parent_start_sec") is None or out.get("parent_end_sec") is None) and window:
        start, end = float(window[0]), float(window[1])
        out["parent_start_sec"] = str(start)
        out["parent_end_sec"] = str(end)
        out["window_s"] = (start, end)
        out.setdefault("dur_s", end - start)
    out.setdefault("source", "ego4d")
    out.setdefault("needs_cut", bool(out.get("needs_cut")))
    out.setdefault("media_time_offset_s", float(out.get("media_time_offset_s") or 0.0))
    return out
