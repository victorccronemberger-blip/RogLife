"""Recovery refresh starts a read; polling must not discard its terminal result."""
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import request

from moneymin import recovery
from moneymin.web import server
from moneymin.web.catalog_loader import CatalogLoader


URL = "/api/recovery?async=1&refresh=1"


@pytest.fixture
def endpoint(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    load = Mock()

    def read():
        entered.set()
        if not release.wait(5):
            raise AssertionError("test did not release recovery read")
        return load()

    snapshot = Mock(side_effect=read)
    monkeypatch.setattr(recovery, "snapshot", snapshot)
    mutations = [Mock(side_effect=AssertionError("GET must not mutate recovery"))
                 for _ in range(2)]
    monkeypatch.setattr(recovery, "reconcile_confirmed", mutations[0])
    worker = {"state": "idle", "email": None}
    cleanup = {"state": "idle"}
    monkeypatch.setattr(server, "RECOVERY", SimpleNamespace(
        snapshot=Mock(return_value=worker), start=mutations[1], running=False))
    monkeypatch.setattr(server, "_wise_cleanup_snapshot", Mock(return_value=cleanup))
    app = server.create_app(for_testing=True)
    app.config["TESTING"] = True
    loader = app.extensions["feature_dependencies"].recovery_catalog
    assert isinstance(loader, CatalogLoader)
    methods = []

    @app.before_request
    def record_method():
        methods.append(request.method)

    state = SimpleNamespace(client=app.test_client(), loader=loader, load=load,
                            snapshot=snapshot, entered=entered, release=release,
                            worker=worker, cleanup=cleanup)
    try:
        yield state
    finally:
        release.set()
        wait_finished(loader)
        for mutation in mutations:
            mutation.assert_not_called()
        assert methods and set(methods) == {"GET"}


def wait_finished(loader):
    deadline = time.monotonic() + 5
    while loader.busy and time.monotonic() < deadline:
        threading.Event().wait(.01)
    assert not loader.busy, "recovery catalog worker did not finish"


def start(endpoint):
    response = endpoint.client.get(URL)
    assert response.status_code == 202
    body = response.get_json()
    assert body["loading"] is True
    assert body["worker"] == endpoint.worker
    assert body["wise_cleanup"] == endpoint.cleanup
    assert endpoint.entered.wait(2)
    return body["job_id"]


@pytest.mark.parametrize("outcome,status", [
    ({"items": [{"session_id": "fixture-session", "email": "fixture@example.invalid",
                 "can_resume": True}], "pending": 1}, 200),
    ({"items": [], "pending": 0}, 200),
    (ValueError("fixture read failure"), 409),
    (OSError("fixture read failure"), 409),
    (RuntimeError("unexpected fixture failure"), 500),
], ids=["result-with-sid", "empty", "value-error", "os-error", "unexpected-error"])
def test_refresh_poll_returns_terminal_result_without_second_load(endpoint, outcome, status):
    if isinstance(outcome, Exception):
        endpoint.load.side_effect = outcome
    else:
        endpoint.load.return_value = outcome
    job_id = start(endpoint)
    # ApiClient preserves refresh=1 when adding job_id, including while running.
    pending = endpoint.client.get(f"{URL}&job_id={job_id}")
    assert pending.status_code == 202
    assert pending.get_json()["job_id"] == job_id
    endpoint.release.set()
    wait_finished(endpoint.loader)

    # The very first GET after completion must deliver the cached terminal result.
    for _ in range(2):
        response = endpoint.client.get(f"{URL}&job_id={job_id}")
        assert response.status_code == status
        body = response.get_json()
        assert not body.get("loading")
        assert body["worker"] == endpoint.worker
        assert body["wise_cleanup"] == endpoint.cleanup
        if status == 200:
            assert body == {**outcome, "worker": endpoint.worker,
                            "wise_cleanup": endpoint.cleanup}
        else:
            assert body["error"]
    endpoint.snapshot.assert_called_once_with()
    endpoint.load.assert_called_once_with()


def test_new_explicit_refresh_without_job_id_still_reloads(endpoint):
    endpoint.load.return_value = {"items": [], "pending": 0}
    first_job = start(endpoint)
    endpoint.release.set()
    wait_finished(endpoint.loader)
    first = endpoint.client.get(f"{URL}&job_id={first_job}")
    assert first.status_code == 200
    # A normal cached read must not refresh either.
    assert endpoint.client.get("/api/recovery?async=1").get_json() == first.get_json()
    endpoint.load.assert_called_once_with()

    endpoint.entered.clear()
    endpoint.release.clear()
    updated = {"items": [{"session_id": "another-fixture-session"}], "pending": 1}
    endpoint.load.return_value = updated
    second_job = start(endpoint)
    assert second_job != first_job
    endpoint.release.set()
    wait_finished(endpoint.loader)
    second = endpoint.client.get(f"{URL}&job_id={second_job}")
    assert second.status_code == 200
    assert second.get_json()["items"] == updated["items"]
    assert endpoint.snapshot.call_count == endpoint.load.call_count == 2
