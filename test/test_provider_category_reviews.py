from __future__ import annotations

import json

from moneymin import provider_category_reviews as reviews


HOLO_ANNOTATION = "a" * 64
HOLO_VIDEO = "b" * 64
NY_SUMMARY = "c" * 40


def _holo_review(**updates):
    review = {
        "status": "confirmed",
        "task_names": ["Furniture Assembly/ Disassembly"],
        "annotation_sha256": HOLO_ANNOTATION,
        "source_video_sha256": HOLO_VIDEO,
        "source_video_bytes": 1234,
        "evidence": {
            "method": "visual sampling",
            "sampled_images": [0.0, 1.0],
            "duration_s": 2.0,
            "description": "Furniture assembly review.",
            "private_path": "C:\\private\\frame.png",
        },
        "internal_url": "https://private.example/frame.png",
    }
    review.update(updates)
    return review


def _ny_review(**updates):
    review = {
        "status": "suggested_from_summary",
        "suggested_task_names": ["Furniture Assembly"],
        "annotation_sha1": NY_SUMMARY,
        "evidence": {
            "method": "summary review",
            "description": "Suggested from source summary.",
            "summary_rows": [2, 5, 8],
            "source_path": "C:\\private\\summary.json",
        },
        "source_video_sha256": HOLO_VIDEO,
    }
    review.update(updates)
    return review


def _write_resource(path, *, holo=None, ny=None, schema=1):
    value = {
        "schema": schema,
        "providers": {
            "holoassist": {"source-1": _holo_review()} if holo is None else holo,
            "nymeria": {"source-1": _ny_review()} if ny is None else ny,
        },
    }
    path.write_text(json.dumps(value), encoding="utf-8")


def _use_resource(monkeypatch, tmp_path, **kwargs):
    path = tmp_path / "reviews.json"
    _write_resource(path, **kwargs)
    monkeypatch.setattr(reviews, "_RESOURCE_PATH", path)
    return path


def test_missing_resource_returns_pending_and_immutable_signature(monkeypatch, tmp_path):
    path = tmp_path / "missing.json"
    monkeypatch.setattr(reviews, "_RESOURCE_PATH", path)

    result = reviews.category_review("holoassist", "source-1", annotation_digest=HOLO_ANNOTATION)

    assert result == {
        "status": "pending",
        "confirmed": False,
        "task_names": [],
        "suggested_task_names": [],
        "evidence_kind": "none",
        "asset_binding": "not_checked",
    }
    signature = reviews.resource_signature()
    assert isinstance(signature, tuple)
    assert signature[0] == str(path.resolve())
    assert signature[1] == "missing"
    assert reviews.has_review("holoassist", "source-1") is False


def test_confirmed_holo_review_is_annotation_bound_and_video_binding_is_explicit(monkeypatch, tmp_path):
    _use_resource(monkeypatch, tmp_path)

    historical = reviews.category_review("holoassist", "source-1", annotation_digest=HOLO_ANNOTATION)
    matched = reviews.category_review(
        "holoassist", "source-1", annotation_digest=HOLO_ANNOTATION, video_digest=HOLO_VIDEO
    )

    assert historical["status"] == "confirmed"
    assert historical["confirmed"] is True
    assert historical["asset_binding"] == "not_checked"
    assert historical["task_names"] == ["Furniture Assembly/ Disassembly"]
    assert historical["source_video_sha256"] == HOLO_VIDEO
    assert historical["source_video_bytes"] == 1234
    assert matched["confirmed"] is True
    assert matched["asset_binding"] == "matched"
    assert "internal_url" not in matched
    assert "private_path" not in matched["evidence"]


def test_missing_or_changed_annotation_digest_fails_closed(monkeypatch, tmp_path):
    _use_resource(monkeypatch, tmp_path)

    missing = reviews.category_review("holoassist", "source-1")
    changed = reviews.category_review("holoassist", "source-1", annotation_digest="d" * 64)

    assert missing["status"] == changed["status"] == "pending"
    assert missing["reason"] == "annotation_digest_required"
    assert changed["reason"] == "annotation_digest_mismatch"
    assert changed["task_names"] == []


def test_changed_video_digest_marks_review_stale(monkeypatch, tmp_path):
    _use_resource(monkeypatch, tmp_path)

    result = reviews.category_review(
        "holoassist", "source-1", annotation_digest=HOLO_ANNOTATION, video_digest="d" * 64
    )

    assert result["status"] == "stale"
    assert result["confirmed"] is False
    assert result["asset_binding"] == "mismatch"
    assert result["reason"] == "source_video_digest_mismatch"
    assert result["task_names"] == []


def test_nymeria_is_only_a_digest_bound_suggestion(monkeypatch, tmp_path):
    _use_resource(monkeypatch, tmp_path)

    result = reviews.category_review("nymeria", "source-1", annotation_digest=NY_SUMMARY)

    assert result["status"] == "suggested_from_summary"
    assert result["confirmed"] is False
    assert result["task_names"] == []
    assert result["suggested_task_names"] == ["Furniture Assembly"]
    assert result["asset_binding"] == "not_checked"
    assert result["evidence"]["summary_rows"] == [2, 5, 8]
    assert "source_video_sha256" not in result
    assert "source_path" not in result["evidence"]
    assert reviews.category_review("nymeria", "source-1", annotation_digest="d" * 40)["status"] == "pending"


def test_review_identity_is_provider_and_source_id_exact(monkeypatch, tmp_path):
    _use_resource(monkeypatch, tmp_path)

    assert reviews.has_review("holoassist", "source-1") is True
    assert reviews.has_review("nymeria", "source-1") is True
    assert reviews.has_review("holoassist", "same-video-hash") is False
    assert reviews.category_review(
        "holoassist", "same-video-hash", annotation_digest=HOLO_ANNOTATION, video_digest=HOLO_VIDEO
    )["status"] == "pending"
    assert reviews.category_review("other-provider", "source-1", annotation_digest=HOLO_ANNOTATION)["status"] == "pending"


def test_returned_nested_data_is_independent_of_cached_resource(monkeypatch, tmp_path):
    _use_resource(monkeypatch, tmp_path)

    first = reviews.category_review("holoassist", "source-1", annotation_digest=HOLO_ANNOTATION)
    first["task_names"].append("Injected")
    first["evidence"]["sampled_images"].append(99)

    second = reviews.category_review("holoassist", "source-1", annotation_digest=HOLO_ANNOTATION)
    assert second["task_names"] == ["Furniture Assembly/ Disassembly"]
    assert second["evidence"]["sampled_images"] == [0.0, 1.0]


def test_resource_cache_tracks_path_and_stat_changes(monkeypatch, tmp_path):
    path = tmp_path / "reviews.json"
    monkeypatch.setattr(reviews, "_RESOURCE_PATH", path)
    path.write_text('{"schema":1,"providers":{}}', encoding="utf-8")
    before = reviews.resource_signature()
    assert reviews.has_review("holoassist", "source-1") is False

    _write_resource(path)
    after = reviews.resource_signature()
    assert after[0] == before[0]
    assert after != before
    assert reviews.has_review("holoassist", "source-1") is True


def test_bad_schema_fails_closed(monkeypatch, tmp_path):
    _use_resource(monkeypatch, tmp_path, schema=2)

    assert reviews.has_review("holoassist", "source-1") is False
    assert reviews.category_review("holoassist", "source-1", annotation_digest=HOLO_ANNOTATION)["status"] == "pending"


def test_bundled_resource_has_only_approved_category_states():
    raw = json.loads(reviews._RESOURCE_PATH.read_text(encoding="utf-8"))
    assert raw["schema"] == 1
    assert len(raw["providers"]["nymeria"]) == 3
    assert len(raw["providers"]["holoassist"]) == 1
    for source_id, item in raw["providers"]["holoassist"].items():
        result = reviews.category_review(
            "holoassist", source_id, annotation_digest=item["annotation_sha256"]
        )
        assert result["confirmed"] is True
        assert result["asset_binding"] == "not_checked"
    for source_id, item in raw["providers"]["nymeria"].items():
        result = reviews.category_review("nymeria", source_id, annotation_digest=item["annotation_sha1"])
        assert result["status"] == "suggested_from_summary"
        assert result["confirmed"] is False
        assert result["task_names"] == []


def test_empty_visual_evidence_or_unknown_canonicalization_cannot_confirm(monkeypatch, tmp_path):
    for updates in ({"source_video_bytes": 0}, {"canonicalization": "unknown"}):
        _use_resource(monkeypatch, tmp_path, holo={"source-1": _holo_review(**updates)})
        assert not reviews.has_review("holoassist", "source-1")
    for field, value in (("sampled_images", 0), ("sampled_images", []), ("duration_s", 0)):
        entry = _holo_review()
        entry["evidence"][field] = value
        _use_resource(monkeypatch, tmp_path, holo={"source-1": entry})
        assert not reviews.category_review("holoassist", "source-1",
            annotation_digest=HOLO_ANNOTATION)["confirmed"]


def test_summary_suggestion_needs_original_positive_row_references(monkeypatch, tmp_path):
    for rows in ([], [0], [-1], [2, 2], [True]):
        entry = _ny_review()
        entry["evidence"]["summary_rows"] = rows
        _use_resource(monkeypatch, tmp_path, ny={"source-1": entry})
        result = reviews.category_review("nymeria", "source-1", annotation_digest=NY_SUMMARY)
        assert result["status"] == "pending"
        assert result["suggested_task_names"] == []
