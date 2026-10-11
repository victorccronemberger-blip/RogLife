"""Prepared catalogue persistence and workflow endpoints, entirely offline."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from flask import Flask
from moneymin import campaign, ego4d, nymeria
from moneymin.catalog_policy import (CatalogPreparationRequired, prepared_catalog_read,
                                    persist_catalog_indexes, persist_index_requested)
from moneymin.web.catalog_loader import CatalogLoader
from moneymin.web.routes import provider_catalogs
import test_nymeria_on_demand_selection as fixture


class PersistentNymeriaIndexTests(unittest.TestCase):
    setUp = fixture.NymeriaOnDemandSelectionTests.setUp
    write_rows = fixture.NymeriaOnDemandSelectionTests.write_rows
    plans = fixture.NymeriaOnDemandSelectionTests.plans
    get_stream_id_from_label = fixture.NymeriaOnDemandSelectionTests.get_stream_id_from_label
    get_metadata = fixture.NymeriaOnDemandSelectionTests.get_metadata
    timestamps = fixture.NymeriaOnDemandSelectionTests.timestamps

    def test_on_demand_prefers_acquired_originals_without_claiming_ready(self):
        data = self.root / "local" / "recording_head" / "data"
        data.mkdir(parents=True)
        (data / "data.vrs").write_bytes(b"rgb fixture")
        clips = [{"clip_uid": "remote", "source": "nymeria", "path": str(self.root / "remote")},
                 {"clip_uid": "ego", "source": "ego4d"},
                 {"clip_uid": "local", "source": "nymeria", "path": str(self.root / "local"),
                  "selection_ready": False, "acquisition_required": True}]
        with patch.object(campaign, "_clip_is_cached", return_value=False):
            self.assertEqual(campaign._prefer_cached_clips(clips, self.root, prioritize=False), clips)
            (data / "motion.vrs").write_bytes(b"imu fixture")
            ordered = campaign._prefer_cached_clips(clips, self.root, prioritize=False)
        self.assertEqual([row["clip_uid"] for row in ordered], ["local", "remote", "ego"])
        self.assertFalse(ordered[0]["selection_ready"])
        self.assertTrue(ordered[0]["acquisition_required"])
        self.assertNotIn("_cache_ready_at_selection", ordered[0])
        self.assertEqual([row["clip_uid"] for row in clips], ["remote", "ego", "local"])

    def test_acquisition_byte_progress_reaches_campaign_monitor(self):
        from moneymin import nymeria_library
        from moneymin.web.runner import CampaignRunner
        events = []
        def acquire(_clip, *, progress, **kwargs):
            progress("Baixando data.vrs: 64/128 bytes")
        with patch.object(nymeria_library, "ensure_sequence_for_campaign", side_effect=acquire), \
             patch.object(nymeria, "resolve_planned_candidate", side_effect=ValueError("fixture ends before measurement")):
            with self.assertRaisesRegex(ValueError, "fixture ends"):
                campaign.prepare_nymeria_clip(self.plans()[0], self.root / "work",
                    progress=lambda phase, payload: events.append((phase, payload)))
        phase, payload = next((phase, payload) for phase, payload in events if payload.get("current") == 64)
        runner = CampaignRunner()
        runner._on_event("clip_prepare_progress", {"phase": phase, **payload})
        self.assertIn("Nymeria", runner.current)
        self.assertIn("50%", runner.current)
        self.assertEqual(runner.stage, "Preparação")

    def test_reviewed_plan_keeps_provider_identity_through_measured_resolution(self):
        from moneymin import campaign_plan
        from moneymin.campaign_types import AccountSpec, CampaignConfig, TaskSpec
        task = TaskSpec("task", "fixture", 300, 1800, task_name=fixture.TASK)
        cfg = CampaignConfig([AccountSpec("fixture@example.invalid", "org")], [task],
                             dataset_provider="nymeria", content_mode="dataset")
        with patch.object(campaign_plan.sent_registry, "load", return_value={}):
            pools, review, _ = campaign_plan.build(cfg, catalog_only=True)
        self.assertTrue(pools[task.task_id])
        self.assertEqual(review[0]["capacity_kind"], "annotation_estimate")
        cfg.candidate_plan = pools
        plan = campaign.automatic_candidates(task, cfg)[0]
        nymeria.revalidate_planned_candidate(plan, root=self.root)
        fixture.NymeriaOnDemandSelectionTests.acquire_fixture(self)
        resolved = nymeria.resolve_planned_candidate(plan, root=self.root)
        self.assertEqual(resolved["device_window_ns"], plan["planned_device_window_ns"])
        self.assertEqual(resolved["acquired_from_planned_clip_uid"], plan["clip_uid"])
        self.assertEqual(nymeria.revalidate_candidate(resolved)["clip_uid"], resolved["clip_uid"])

    def prepare(self):
        with ego4d.selection_operation(), persist_catalog_indexes():
            return self.plans()

    def test_restart_reuses_index_without_classifying_or_opening_vrs(self):
        expected = self.prepare()
        self.assertTrue(expected)
        nymeria.clear_caches()
        with ego4d.selection_operation(), prepared_catalog_read(), \
             patch.object(nymeria, '_planned_sequence_batch', side_effect=AssertionError('cold work')), \
             patch.object(nymeria, '_snapshot', side_effect=AssertionError('VRS')):
            self.assertEqual(self.plans(), expected)

    def test_prepared_read_never_classifies_missing_corrupt_or_changed_index(self):
        with ego4d.selection_operation(), prepared_catalog_read(), \
             patch.object(nymeria, '_planned_sequence_batch', side_effect=AssertionError('cold work')):
            with self.assertRaises(CatalogPreparationRequired):
                self.plans()
        self.prepare()
        path = next((self.root / '_catalog').glob('task_selection_*.json'))
        saved = path.read_bytes()
        for content in (b'{}', b'null', b'{bad json', b'{"schema":1,"candidates":[null]}'):
            path.write_bytes(content)
            nymeria.clear_caches()
            with ego4d.selection_operation(), prepared_catalog_read():
                with self.assertRaises(CatalogPreparationRequired):
                    self.plans()
        path.write_bytes(saved)
        annotation = self.narration / 'atomic_action.csv'
        original_stat = annotation.stat()
        annotation.write_bytes(annotation.read_bytes().replace(b'shirt', b'pants'))
        os.utime(annotation, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        nymeria.clear_caches()
        with ego4d.selection_operation(), prepared_catalog_read():
            with self.assertRaises(CatalogPreparationRequired):
                self.plans()

    def test_index_written_once_per_operation_and_duration_is_bound(self):
        from moneymin import nymeria_library
        with patch.object(nymeria_library, '_write_json', wraps=nymeria_library._write_json) as writer, \
             ego4d.selection_operation(), persist_catalog_indexes():
            self.plans(); self.plans()
            self.assertEqual(writer.call_count, 1)
        nymeria.clear_caches()
        with ego4d.selection_operation(), prepared_catalog_read():
            with self.assertRaises(CatalogPreparationRequired):
                nymeria.planned_candidates(task_name=fixture.TASK, min_dur_s=60,
                                           max_dur_s=300, root=self.root)
        self.assertFalse(persist_index_requested())


class ProviderCatalogEndpointTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.app = Flask(__name__)
        self.loader = CatalogLoader(timeout_s=300)
        self.entered, self.release = threading.Event(), threading.Event()
        self.calls = []
        def compatible(name, provider, **kwargs):
            self.assertTrue(persist_index_requested())
            self.calls.append((name, provider, kwargs))
            self.entered.set(); self.release.wait(3)
            return [{'dur_s': 60}]
        self.domain = SimpleNamespace(normalize_dataset_provider=lambda value: value,
            nymeria=SimpleNamespace(_rules_digest=lambda: 'rules', data_root=lambda: self.root),
            ego4d=ego4d, task_matching=SimpleNamespace(TASK_RULES={'Fixture': object()}),
            _compatible_task_clips=compatible, _catalog_queue_accepts=lambda *_: True)
        from flask import request, jsonify
        self.services = SimpleNamespace(campaign=self.domain, task_catalog=self.loader,
            request=request, jsonify=jsonify,
            config=SimpleNamespace(DATA_DIR=self.root, MEDIA_DATA_DIR=self.root / 'library'),
            _parse_duration_range=lambda args: (float(args.get('min_dur_s', 60)),
                                               float(args.get('max_dur_s', 1800))))
        self.app.add_url_rule('/api/catalogs/providers', endpoint='catalog_status', view_func=lambda:
            provider_catalogs.get_provider_catalog(self.services))
        self.app.add_url_rule('/api/catalogs/providers/prepare', endpoint='catalog_prepare', methods=['POST'], view_func=lambda:
            provider_catalogs.prepare_provider_catalog(self.services))
        self.client = self.app.test_client()
        self.addCleanup(self.finish)

    def finish(self):
        self.release.set()
        with self.loader._worker:
            pass

    def test_prepare_is_one_worker_and_poll_is_selection_bound(self):
        initial = self.client.post('/api/catalogs/providers/prepare?dataset=nymeria')
        self.assertEqual(initial.status_code, 202)
        self.assertTrue(self.entered.wait(1))
        job = initial.json['job_id']
        again = self.client.post('/api/catalogs/providers/prepare?dataset=nymeria')
        self.assertEqual(again.json['job_id'], job)
        self.assertEqual(len(self.calls), 1)
        wrong = self.client.get(f'/api/catalogs/providers?dataset=ego4d&job_id={job}')
        self.assertEqual(wrong.status_code, 409)
        pending = self.client.get(f'/api/catalogs/providers?dataset=nymeria&job_id={job}')
        self.assertEqual(pending.status_code, 202)
        self.release.set()
        with self.loader._worker:
            pass
        final = self.client.get(f'/api/catalogs/providers?dataset=nymeria&job_id={job}')
        self.assertEqual(final.status_code, 200, final.json)
        self.assertTrue(final.json['ready'])
        self.assertFalse(final.json['media_downloaded'])
        self.assertEqual(final.json['providers'][0]['candidates'], 1)

    def test_status_never_starts_work_and_invalid_provider_rejected(self):
        self.assertEqual(self.client.get('/api/catalogs/providers').status_code, 200)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.client.post('/api/catalogs/providers/prepare?dataset=bad').status_code, 400)


def test_queued_catalog_wait_does_not_expire_at_running_idle_deadline():
    loader = CatalogLoader(timeout_s=300)
    row = {'state':'queued', 'started':100., 'last_progress':100., 'message':'queued',
           'phase':'queued', 'job_id':'fixture'}
    assert loader._response(row, 700.)[1] == 202
    assert loader._response(row, 1901.)[1] == 504
    row['state'] = 'running'
    assert loader._response(row, 700.)[1] == 504
