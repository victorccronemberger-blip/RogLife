"""Measured campaign catalog progress; no network, accounts or real media."""
import threading
import time
from unittest.mock import patch

from moneymin import campaign
from moneymin.background_work import responsive_catalog_work
from moneymin.web.catalog_loader import CatalogLoader


def test_catalog_poll_reports_real_counts_without_restarting_the_worker():
    loader = CatalogLoader()
    entered, release = threading.Event(), threading.Event()

    def work(progress):
        progress("Conferindo categoria 2/4", phase="campaign_categories", completed=1, total=4)
        entered.set()
        release.wait(3)
        return {"tasks": []}, 200

    try:
        initial, _ = loader.get(("fixture",), work)
        assert entered.wait(1)
        for _ in range(3):
            body, status = loader.poll(initial["job_id"], key=("fixture",))
            assert status == 202
            assert (body["completed"], body["total"]) == (1, 4)
            assert body["elapsed_s"] >= 0 and body["idle_s"] >= 0
        assert len(loader._jobs) == 1
    finally:
        release.set()


def test_category_progress_does_not_change_the_catalog_results():
    observed = []
    tasks = [{"id": "garden", "name": "Gardening"},
             {"id": "unknown", "name": "Unmapped fixture activity"}]
    with responsive_catalog_work(progress=lambda message, **fields: observed.append((message, fields))), \
         patch.object(campaign, "_compatible_task_clips", return_value=()):
        rows = campaign.available_tasks("fixture@example.invalid", "fixture-org",
            remote_tasks=tasks, dataset_provider="nymeria", content_mode="dataset",
            include_unavailable=True)
    counts = [fields for _, fields in observed if "completed" in fields]
    assert [(row["completed"], row["total"]) for row in counts] == [(0, 2), (1, 2)]
    assert [row["id"] for row in rows] == ["garden", "unknown"]
    assert all(row["clip_count"] == 0 for row in rows)
