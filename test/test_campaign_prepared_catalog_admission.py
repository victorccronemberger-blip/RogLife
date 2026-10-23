"""Dataset admission reads prepared catalogues; all accounts/media are inert."""
from contextlib import ExitStack
import json
import time
import unittest
import uuid
from unittest.mock import Mock, patch

from moneymin import campaign, ego4d, nymeria, recovery
from moneymin.catalog_policy import persist_catalog_indexes, prepared_only_requested
from moneymin.web import server
import test_ego4d_selection_bindings as selection_fixture
import test_nymeria_on_demand_selection as nymeria_fixture
from auth_fixtures import configure_app_check


class CampaignPreparedCatalogAdmissionTests(unittest.TestCase):
    same_stat_replace = selection_fixture.SelectionBindingTests.same_stat_replace

    def setUp(self):
        configure_app_check(self)
        selection_fixture.SelectionBindingTests.setUp(self)
        secrets = self.root / "secrets"
        secrets.mkdir()
        for name, value in (("ROOT", self.root), ("LIBRARY_ROOT", self.root),
                            ("SECRETS_DIR", secrets)):
            self.stack.enter_context(patch.object(server.config, name, value))
        for name, filename in (("PREFS_PATH", "prefs.json"), ("BALANCES_PATH", "balances.json"),
                               ("CROWTADO_PW_PATH", "passwords.json"), ("ACCOUNT_HEALTH_PATH", "health.json")):
            self.stack.enter_context(patch.object(server, name, self.root / filename))
        self.email = "fixture-owner@example.invalid"
        server.config.token_path(self.email).write_text(json.dumps({
            "email": self.email, "localId": "fixture-subject",
            "idToken": "inert-token", "refreshToken": "inert-refresh"}))
        self.stack.enter_context(patch.object(server, "_list_accounts", return_value=[{"email": self.email}]))
        self.resolve = self.stack.enter_context(patch.object(server, "_resolve_org", return_value="fixture-org"))
        self.stack.enter_context(patch.object(server.readiness, "campaign_readiness",
                                             return_value={"ready": True, "checks": []}))
        self.stack.enter_context(patch.object(server, "_storage_snapshot", return_value={"free_bytes": 100 * 1024**3}))
        self.stack.enter_context(patch.object(recovery, "snapshot", return_value={"items": []}))
        self.stack.enter_context(patch.object(server, "RECOVERY", Mock(running=False)))
        for name in ("HOLO_CACHE_RUNNER", "BALANCES_RUNNER", "ORG_MIGRATION"):
            self.stack.enter_context(patch.object(server, name, Mock(running=False)))
        self.runner = Mock(running=False, state="idle", total_sends=1)
        self.stack.enter_context(patch.object(server, "RUNNER", self.runner))
        self.ban = self.stack.enter_context(patch.object(server, "_ban_accounts"))
        self.session = Mock()
        self.session.check_recording_authorization.return_value = None
        self.session.all_tasks.return_value = [{"id": "fixture-task", "name": self.task}]
        self.stack.enter_context(patch.object(server.Session, "from_email", return_value=self.session))
        self.ensure = self.stack.enter_context(patch.object(ego4d, "ensure_task_annotations"))
        self.classify = self.stack.enter_context(patch.object(
            campaign.task_matching, "rank_all_tasks", wraps=campaign.task_matching.rank_all_tasks))
        self.app = server.create_app(for_testing=True)
        self.client = self.app.test_client()
        self.body = {"accounts": [self.email], "tasks": [{"task_id": "fixture-task"}],
                     "dataset": "ego4d", "content_mode": "dataset", "min_dur_s": 300, "max_dur_s": 1800}

    def prepare(self):
        with persist_catalog_indexes():
            campaign._ranked_pools()
            campaign._compatible_task_clips(self.task, "ego4d", min_dur_s=300,
                                            max_dur_s=1800, catalog_only=True)
        self.assertIsNotNone(campaign._load_rank_cache())
        self.classify.reset_mock()
        self.ensure.reset_mock()

    def assert_preparation_required(self, response):
        self.classify.assert_not_called()
        self.ensure.assert_not_called()
        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual(response.json["code"], "provider_catalog_preparation_required")
        self.assertIn("Catálogos", response.json["error"])
        self.runner.start.assert_not_called()
        self.ban.assert_not_called()
        if "account_issues" in response.json:
            self.assertEqual(response.json["account_issues"], [])
            self.assertIsNone(response.json["preflight_id"])
            self.assertFalse(response.json["can_skip_and_continue"])
        self.assertFalse(prepared_only_requested(), "prepared policy leaked out of admission")

    def test_returning_after_preparation_does_not_reuse_cached_missing_index_error(self):
        url = (f'/api/tasks?async=1&prepared_only=1&email={self.email}'
               '&dataset=ego4d&content_mode=dataset&min_dur_s=300&max_dur_s=1800')

        def completed():
            response = self.client.get(url)
            deadline = time.monotonic() + 5
            while response.status_code == 202 and time.monotonic() < deadline:
                job = response.json.get('job_id')
                time.sleep(.01)
                response = self.client.get(url + '&job_id=' + job) if job else self.client.get(url)
            return response

        self.assert_preparation_required(completed())
        self.prepare()
        # Same account, source and duration; the cached failure is still within
        # its TTL. A navigation read must now observe the published index.
        response = completed()
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.json['tasks'])
        self.classify.assert_not_called()
        self.ensure.assert_not_called()

    def test_preflight_missing_index_rejects_before_cold_classification(self):
        self.assert_preparation_required(self.client.post("/api/campaigns/preflight", json=self.body))

    def test_base_index_serves_one_to_two_minutes_catalog_and_preflight_after_restart(self):
        self.clips.write_text(self.clips.read_text(encoding="utf-8").replace(
            "c0,parent,0,600,", "c0,parent,0,90,"), encoding="utf-8")
        with persist_catalog_indexes():
            campaign._ranked_pools()
        self.assertEqual([path.name for path in self.root.glob("task_rank_cache*.pkl")],
                         ["task_rank_cache.pkl"])
        campaign._ranked_pools_cached.cache_clear()
        campaign._duration_ranked_pools.cache_clear()
        self.classify.reset_mock()
        self.ensure.reset_mock()
        with patch.object(campaign, "_duration_window_candidates", side_effect=AssertionError("new cuts")), \
             patch.object(campaign, "_save_rank_cache", side_effect=AssertionError("persist")):
            url = (f"/api/tasks?async=1&prepared_only=1&email={self.email}"
                   "&dataset=ego4d&content_mode=dataset&min_dur_s=60&max_dur_s=120")
            catalog = self.client.get(url)
            deadline = time.monotonic() + 5
            while catalog.status_code == 202 and time.monotonic() < deadline:
                job = catalog.json["job_id"]
                time.sleep(.01)
                catalog = self.client.get(url + "&job_id=" + job)
            self.assertEqual(catalog.status_code, 200, catalog.get_json())
            self.assertTrue(catalog.json["tasks"])
            self.assertTrue(catalog.json["tasks"][0]["available_for_duration"])
            preview = self.client.post("/api/campaigns/preflight", json={
                **self.body, "min_dur_s": 60, "max_dur_s": 120, "include_clip_plan": True})
            self.assertEqual(preview.status_code, 200, preview.get_json())
            self.assertTrue(preview.json["ok"], preview.json)
            self.assertTrue(preview.json["preflight_id"])
        self.classify.assert_not_called()
        self.ensure.assert_not_called()
        self.runner.start.assert_not_called()

    def test_unreviewed_start_missing_index_rejects_before_cold_classification(self):
        self.assert_preparation_required(self.client.post("/api/campaigns", json=self.body))

    def test_preflight_stale_same_stat_source_rejects_before_cold_classification(self):
        self.prepare()
        self.same_stat_replace(self.actions, b"shirt", b"tacos")
        self.assertIsNone(campaign._load_rank_cache())
        self.assert_preparation_required(self.client.post("/api/campaigns/preflight", json=self.body))

    def test_unreviewed_start_stale_same_stat_source_rejects_before_cold_classification(self):
        self.prepare()
        self.same_stat_replace(self.actions, b"shirt", b"tacos")
        self.assertIsNone(campaign._load_rank_cache())
        self.assert_preparation_required(self.client.post("/api/campaigns", json=self.body))

    def test_prepared_preflight_and_reviewed_start_keep_exact_receipt(self):
        self.prepare()
        preview = self.client.post("/api/campaigns/preflight", json=self.body)
        self.assertEqual(preview.status_code, 200, preview.get_json())
        self.assertTrue(preview.json["ok"], preview.json)
        receipt_id = preview.json["preflight_id"]
        self.assertTrue(receipt_id)
        # Admission of an approved receipt reuses its server-owned catalogue.
        # Do not reread/reclassify a mutable source or change the start UUID.
        self.same_stat_replace(self.actions, b"shirt", b"tacos")
        response = self.client.post("/api/campaigns", json={**self.body, "preflight_id": receipt_id})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.runner.start.assert_called_once()
        cfg = self.runner.start.call_args.args[0]
        self.assertEqual(cfg.start_request_id, receipt_id)
        self.assertEqual([task.task_id for task in cfg.tasks], ["fixture-task"])
        self.classify.assert_not_called()
        self.ensure.assert_not_called()

    def test_prepared_unreviewed_start_reads_index_without_classifying(self):
        self.prepare()
        response = self.client.post("/api/campaigns", json=self.body)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.runner.start.assert_called_once()
        self.assertIsNone(self.runner.start.call_args.args[0].start_request_id)
        self.classify.assert_not_called()
        self.ensure.assert_not_called()

    def test_preview_source_change_after_categories_requires_catalog_update(self):
        self.prepare()
        available_tasks = campaign.available_tasks

        def change_source_after_catalog(*args, **kwargs):
            rows = available_tasks(*args, **kwargs)
            self.same_stat_replace(self.actions, b"shirt", b"tacos")
            return rows

        with patch.object(campaign, "available_tasks", side_effect=change_source_after_catalog):
            self.assert_preparation_required(self.client.post(
                "/api/campaigns/preflight", json={**self.body, "include_clip_plan": True}))

    def test_hours_start_source_change_after_categories_requires_catalog_update(self):
        self.prepare()
        available_tasks = campaign.available_tasks

        def change_source_after_catalog(*args, **kwargs):
            rows = available_tasks(*args, **kwargs)
            self.same_stat_replace(self.actions, b"shirt", b"tacos")
            return rows

        with patch.object(campaign, "available_tasks", side_effect=change_source_after_catalog):
            response = self.client.post("/api/campaigns", json={
                **self.body, "target_hours": .1, "run_until_exhausted": False})
        self.assert_preparation_required(response)
        self.assertIsNone(response.json["capacity"])

    def test_hours_reviewed_start_keeps_frozen_plan_after_source_change(self):
        self.prepare()
        body = {**self.body, "target_hours": .1, "run_until_exhausted": False,
                "include_clip_plan": True}
        preview = self.client.post("/api/campaigns/preflight", json=body)
        self.assertEqual(preview.status_code, 200, preview.get_json())
        self.assertTrue(preview.json["ok"], preview.json)
        receipt_id = preview.json["preflight_id"]
        plan = self.app.extensions["feature_dependencies"].preflights[receipt_id]["candidate_plan"]
        self.same_stat_replace(self.actions, b"shirt", b"tacos")
        response = self.client.post("/api/campaigns", json={**body, "preflight_id": receipt_id})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.runner.start.assert_called_once()
        cfg = self.runner.start.call_args.args[0]
        self.assertEqual(cfg.start_request_id, receipt_id)
        self.assertEqual(cfg.candidate_plan, plan)
        self.classify.assert_not_called()
        self.ensure.assert_not_called()

    def test_nymeria_missing_and_stale_index_rejects_without_classifying(self):
        fixture = nymeria_fixture.NymeriaOnDemandSelectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        body = {**self.body, "dataset": "nymeria"}
        with patch.object(nymeria, "_planned_sequence_batch", wraps=nymeria._planned_sequence_batch) as classify, \
             patch.object(nymeria, "_snapshot", side_effect=AssertionError("preview opened VRS")):
            for stale in (False, True):
                if stale:
                    with ego4d.selection_operation(), persist_catalog_indexes():
                        nymeria.planned_candidates(task_name=self.task, min_dur_s=60, max_dur_s=1800,
                                                  root=fixture.root)
                        nymeria.planned_candidates(task_name=self.task, min_dur_s=300, max_dur_s=1800,
                                                  root=fixture.root)
                    fixture.write_rows(nymeria_fixture.ACTION + " Changed fixture annotation.")
                    nymeria.clear_caches()
                    classify.reset_mock()
                for route in ("/api/campaigns/preflight", "/api/campaigns"):
                    with self.subTest(stale=stale, route=route):
                        self.assert_preparation_required(self.client.post(route, json=body))
                        classify.assert_not_called()

    def test_nymeria_change_after_catalog_never_approves_partial_plan(self):
        self.prepare()
        fixture = nymeria_fixture.NymeriaOnDemandSelectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        available_tasks = campaign.available_tasks

        def change_source_after_catalog(*args, **kwargs):
            rows = available_tasks(*args, **kwargs)
            self.assertGreater(rows[0]["clip_sources"].get("nymeria", 0), 0)
            if kwargs["dataset_provider"] != "nymeria":
                self.assertGreater(rows[0]["clip_sources"].get("ego4d", 0), 0)
            self.same_stat_replace(fixture.narration / "atomic_action.csv", b"shirt", b"pants")
            nymeria.clear_caches()
            return rows

        with patch.object(campaign, "available_tasks", side_effect=change_source_after_catalog), \
             patch.object(campaign.holoassist, "list_clips", return_value=[]), \
             patch.object(nymeria, "_planned_sequence_batch", wraps=nymeria._planned_sequence_batch) as classify, \
             patch.object(nymeria, "_snapshot", side_effect=AssertionError("preview opened VRS")), \
             patch("moneymin.imu_coverage.refine_candidates", side_effect=lambda clips, *_: clips):
            for provider in ("nymeria", "ambos", "all"):
                for route in ("/api/campaigns/preflight", "/api/campaigns"):
                    with self.subTest(provider=provider, route=route):
                        fixture.write_rows()
                        nymeria.clear_caches()
                        with ego4d.selection_operation(), persist_catalog_indexes():
                            for minimum in (60, 300):
                                self.assertTrue(nymeria.planned_candidates(
                                    task_name=self.task, min_dur_s=minimum, max_dur_s=1800, root=fixture.root))
                        classify.reset_mock()
                        self.runner.start.reset_mock()
                        response = self.client.post(route, json={
                            **self.body, "dataset": provider, "include_clip_plan": True,
                            "target_hours": .1, "run_until_exhausted": False})
                        self.assert_preparation_required(response)
                        classify.assert_not_called()

    def test_cache_and_both_unreviewed_legacy_admission_keep_cold_lookup(self):
        for mode in ("cache", "both"):
            for route in ("/api/campaigns/preflight", "/api/campaigns"):
                with self.subTest(mode=mode, route=route), ExitStack() as stack:
                    campaign._rank_cache_path().unlink(missing_ok=True)
                    campaign._ranked_pools_cached.cache_clear()
                    self.classify.reset_mock()
                    self.ensure.reset_mock()
                    self.runner.start.reset_mock()
                    stack.enter_context(patch("moneymin.ego_accelerator.ready_scenario_clips", return_value=[]))
                    stack.enter_context(patch.object(campaign, "_catalog_clip_cached_hint", return_value=True))
                    response = self.client.post(route, json={**self.body, "content_mode": mode})
                    self.assertEqual(response.status_code, 200, response.get_json())
                    self.assertTrue(response.json["ok"], response.json)
                    self.assertGreater(self.classify.call_count, 0)
                    self.assertGreater(self.ensure.call_count, 0)
                    self.assertFalse(prepared_only_requested())

    def test_async_preflight_worker_applies_policy_locally(self):
        path = "/api/campaigns/preflight?async=1&request_id=" + uuid.uuid4().hex
        response = self.client.post(path, json=self.body)
        deadline = time.monotonic() + 5
        while response.status_code == 202 and time.monotonic() < deadline:
            time.sleep(.01)
            response = self.client.post(path, json=self.body)
        self.assert_preparation_required(response)
