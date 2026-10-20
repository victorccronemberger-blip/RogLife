"""Prepared catalogue persistence and workflow endpoints, entirely offline."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import threading
import time
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
import test_ego4d_selection_bindings as ego_fixture
import test_nymeria_library as library_fixture


class PersistentEgoIndexTests(unittest.TestCase):
    setUp = ego_fixture.SelectionBindingTests.setUp

    def test_explicit_prepare_persists_warm_indexes_then_restart_reuses_them(self):
        with patch.object(campaign, '_save_rank_cache'):
            expected = campaign._ranked_pools_cached()
            ranged = campaign._duration_ranked_pools(300, 1800)
        self.assertFalse(campaign._rank_cache_path().exists())
        with persist_catalog_indexes(), patch.object(ego4d, 'rank_all_task_spans',
                side_effect=AssertionError('warm cache reclassified')):
            self.assertEqual(campaign._ranked_pools_cached(), expected)
            self.assertEqual(campaign._duration_ranked_pools(300, 1800), ranged)
        campaign._ranked_pools_cached.cache_clear()
        campaign._duration_ranked_pools.cache_clear()
        with prepared_catalog_read(), patch.object(ego4d, 'rank_all_task_spans',
                side_effect=AssertionError('restart reclassified')):
            self.assertEqual(json.dumps(campaign._ranked_pools_cached(), sort_keys=True),
                             json.dumps(expected, sort_keys=True))
            self.assertEqual(json.dumps(campaign._duration_ranked_pools(300, 1800), sort_keys=True),
                             json.dumps(ranged, sort_keys=True))

    def test_explicit_prepare_does_not_hide_disk_write_failure(self):
        with persist_catalog_indexes(), patch.object(Path, 'replace', side_effect=OSError('fixture disk')):
            with self.assertRaises(OSError):
                campaign._save_rank_cache({self.task: ()})


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

    def test_identical_annotation_replacement_reuses_persisted_index(self):
        expected = self.prepare()
        for path in (self.seq / 'metadata.json', self.narration / 'atomic_action.csv'):
            original = path.read_bytes()
            info = path.stat()
            replacement = path.with_suffix('.replacement')
            replacement.write_bytes(original)
            replacement.replace(path)
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 10_000_000_000))
        nymeria.clear_caches()
        with prepared_catalog_read(), patch.object(nymeria, '_planned_sequence_batch',
                side_effect=AssertionError('unchanged content reclassified')):
            self.assertEqual(self.plans(), expected)

    def test_prepared_base_serves_unprepared_range_without_changing_proof(self):
        with persist_catalog_indexes():
            base = nymeria.planned_candidates(task_name=fixture.TASK, root=self.root)
        self.assertTrue(base)
        files = set((self.root / '_catalog').glob('task_selection_*.json'))
        nymeria.clear_caches()
        with prepared_catalog_read(), patch.object(nymeria, '_planned_sequence_batch',
                side_effect=AssertionError('filtered read reclassified')), \
             patch.object(nymeria, '_snapshot', side_effect=AssertionError('VRS opened')):
            actual = nymeria.planned_candidates(task_name=fixture.TASK,
                min_dur_s=120, max_dur_s=540, root=self.root)
            self.assertEqual(actual, [clip for clip in base if 120 <= clip['dur_s'] <= 540])
            self.assertTrue(actual)
            limited = nymeria.planned_candidates(task_name=fixture.TASK,
                min_dur_s=120, max_dur_s=540, root=self.root, max_results=0)
            self.assertEqual(limited, [])
        self.assertEqual(set((self.root / '_catalog').glob('task_selection_*.json')), files)
        for clip in actual:
            self.assertEqual(nymeria.revalidate_planned_candidate(clip, root=self.root), clip)

    def test_filtered_base_does_not_replace_explicit_duration_preparation(self):
        with persist_catalog_indexes():
            nymeria.planned_candidates(task_name=fixture.TASK, root=self.root)
        nymeria.clear_caches()
        with prepared_catalog_read():
            filtered = nymeria.planned_candidates(task_name=fixture.TASK,
                min_dur_s=60, max_dur_s=300, root=self.root)
        self.assertEqual(filtered, [])
        with persist_catalog_indexes():
            prepared = nymeria.planned_candidates(task_name=fixture.TASK,
                min_dur_s=60, max_dur_s=300, root=self.root)
        self.assertTrue(prepared)
        nymeria.clear_caches()
        with prepared_catalog_read(), patch.object(nymeria, '_planned_sequence_batch',
                side_effect=AssertionError('prepared variant reclassified')):
            self.assertEqual(nymeria.planned_candidates(task_name=fixture.TASK,
                min_dur_s=60, max_dur_s=300, root=self.root), prepared)

    def test_base_fallback_rejects_changed_annotation_content(self):
        with persist_catalog_indexes():
            nymeria.planned_candidates(task_name=fixture.TASK, root=self.root)
        self.write_rows(text=fixture.ACTION.replace('shirt', 'pants'))
        nymeria.clear_caches()
        with prepared_catalog_read(), patch.object(nymeria, '_planned_sequence_batch',
                side_effect=AssertionError('changed source reclassified')):
            with self.assertRaises(CatalogPreparationRequired):
                nymeria.planned_candidates(task_name=fixture.TASK,
                    min_dur_s=120, max_dur_s=540, root=self.root)

    def test_base_fallback_does_not_claim_ranges_outside_its_coverage(self):
        with persist_catalog_indexes():
            nymeria.planned_candidates(task_name=fixture.TASK, root=self.root)
        nymeria.clear_caches()
        with prepared_catalog_read():
            for minimum, maximum in ((30, 540), (60, 3600)):
                with self.subTest(minimum=minimum, maximum=maximum):
                    with self.assertRaises(CatalogPreparationRequired):
                        nymeria.planned_candidates(task_name=fixture.TASK,
                            min_dur_s=minimum, max_dur_s=maximum, root=self.root)

    def test_legacy_stat_bound_index_still_reads_without_repreparing(self):
        expected = self.prepare()
        path = next((self.root / '_catalog').glob('task_selection_*.json'))
        saved = json.loads(path.read_text('utf8'))
        saved.pop('content_binding')
        path.write_text(json.dumps(saved), encoding='utf8')
        nymeria.clear_caches()
        with prepared_catalog_read(), patch.object(nymeria, '_planned_sequence_batch',
                side_effect=AssertionError('legacy index reclassified')):
            self.assertEqual(self.plans(), expected)

    def test_campaign_adapter_does_not_hide_index_write_failure(self):
        from moneymin import nymeria_library
        with persist_catalog_indexes(), patch.object(nymeria_library, '_write_json',
                side_effect=OSError('fixture disk')):
            with self.assertRaises(OSError):
                campaign._compatible_task_clips(fixture.TASK, 'nymeria',
                    min_dur_s=300, max_dur_s=1800, catalog_only=True)

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
            ego4d=SimpleNamespace(selection_operation=ego4d.selection_operation,
                                 ensure_task_annotations=Mock()),
            _local_narration_catalog=lambda: True,
            task_matching=SimpleNamespace(TASK_RULES={'Fixture': object()}),
            _compatible_task_clips=compatible, _catalog_queue_accepts=lambda *_: True)
        self.sources = SimpleNamespace(sync_catalog=Mock(return_value={"sync": {
            "states": {"fixture": "cataloged"}, "errors": {}, "completed": 1, "total": 1}}))
        from flask import request, jsonify
        self.services = SimpleNamespace(campaign=self.domain, task_catalog=self.loader,
            nymeria_library=self.sources,
            _HEAVY_RUNNER_LOCK=threading.Lock(),
            RUNNER=SimpleNamespace(running=False), RECOVERY=SimpleNamespace(running=False),
            HOLO_CACHE_RUNNER=SimpleNamespace(running=False),
            nymeria_library_worker=SimpleNamespace(running=False),
            campaign_drain={"requested": False, "requests": 0},
            campaign_verifications=SimpleNamespace(busy=False), nymeria_catalog=SimpleNamespace(busy=False),
            campaign_closing_response=lambda: (jsonify({"error_code": "campaign_closing"}), 409),
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
        self.sources.sync_catalog.assert_not_called()
        self.assertEqual(self.client.post('/api/catalogs/providers/prepare?dataset=bad').status_code, 400)

    def test_status_finds_base_index_for_other_duration_without_claiming_ready(self):
        import hashlib
        path = self.root / '_catalog' / ('task_selection_' +
            hashlib.sha256(b'60.0|1800.0').hexdigest()[:16] + '.json')
        path.parent.mkdir()
        path.write_text('{}', encoding='utf8')
        result = self.client.get('/api/catalogs/providers?dataset=nymeria&min_dur_s=120&max_dur_s=540')
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json['indexes_found']['nymeria'])
        self.assertEqual(result.json['state'], 'idle')
        self.assertNotIn('ready', result.json)
        self.assertEqual(self.calls, [])
        self.sources.sync_catalog.assert_not_called()

    def completed(self, provider='nymeria'):
        initial = self.client.post(f'/api/catalogs/providers/prepare?dataset={provider}')
        self.release.set()
        deadline = time.monotonic() + 5
        while self.loader.busy and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.loader.busy)
        return self.client.get(f'/api/catalogs/providers?dataset={provider}&job_id={initial.json["job_id"]}')

    def test_passive_status_recovers_current_job_and_its_source_progress(self):
        syncing, continue_sync = threading.Event(), threading.Event()
        def sync(*, progress):
            progress('Catálogo Nymeria: 1/3 sequências')
            syncing.set()
            continue_sync.wait(3)
            return {"sync": {"states": {"fixture": "cataloged"}, "errors": {},
                             "completed": 3, "total": 3}}
        self.sources.sync_catalog.side_effect = sync
        try:
            initial = self.client.post('/api/catalogs/providers/prepare?dataset=nymeria')
            self.assertTrue(syncing.wait(1))
            status = self.client.get('/api/catalogs/providers?dataset=nymeria')
            self.assertEqual(status.status_code, 202)
            self.assertEqual(status.json['job_id'], initial.json['job_id'])
            self.assertEqual(status.json['phase'], 'nymeria_source_sync')
            self.assertEqual((status.json['completed'], status.json['total']), (1, 3))
            self.assertEqual(self.calls, [])
            different = self.client.get('/api/catalogs/providers?dataset=nymeria&min_dur_s=300')
            self.assertEqual(different.json['state'], 'idle')
            self.sources.sync_catalog.assert_called_once()
        finally:
            continue_sync.set()

    def test_missing_sources_never_return_ready_or_start_classification(self):
        self.sources.sync_catalog.return_value = {"sync": {"states": {}, "errors": {},
                                                           "completed": 0, "total": 0}}
        result = self.completed()
        self.assertEqual(result.status_code, 409)
        self.assertFalse(result.json['ready'])
        self.assertEqual(result.json['providers'][0]['phase'], 'sources')
        self.assertEqual(self.calls, [])

    def test_partial_download_is_reported_and_successful_sources_are_indexed(self):
        self.sources.sync_catalog.return_value = {"sync": {
            "states": {"good": "cataloged", "bad": "download_failed"},
            "errors": {"bad": "fixture"}, "completed": 2, "total": 2}}
        result = self.completed()
        self.assertEqual(result.status_code, 409)
        self.assertFalse(result.json['ready'])
        self.assertEqual(result.json['state'], 'partial')
        self.assertEqual(result.json['providers'][0]['sources']['errors'], 1)
        self.assertEqual(result.json['providers'][0]['sources']['download_failed'], 1)
        self.assertEqual(result.json['providers'][0]['sources']['invalid_annotations'], 0)
        self.assertEqual(result.json['providers'][0]['candidates'], 1)

    def test_sources_without_atomic_annotations_are_counted_without_blocking_valid_index(self):
        self.sources.sync_catalog.return_value = {"sync": {
            "states": {**{f"good-{index}": "cataloged" for index in range(845)},
                       **{f"auxiliary-{index}": "no_annotations" for index in range(255)}},
            "errors": {}, "completed": 1100, "total": 1100}}
        result = self.completed()
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json['ready'])
        sources = result.json['providers'][0]['sources']
        self.assertEqual(sources['cataloged'], 845)
        self.assertEqual(sources['no_annotations'], 255)
        self.assertEqual(sources['invalid_annotations'], 0)
        self.assertEqual(sources['download_failed'], 0)
        self.assertEqual(sources['errors'], 0)
        self.assertEqual(sources['completed'], sources['total'])

    def test_genuinely_invalid_atomic_annotations_still_block_success(self):
        self.sources.sync_catalog.return_value = {"sync": {
            "states": {"good": "cataloged", "bad": "invalid_annotations", "empty": "no_annotations"},
            "errors": {}, "completed": 3, "total": 3}}
        result = self.completed()
        self.assertEqual(result.status_code, 409)
        self.assertFalse(result.json['ready'])
        sources = result.json['providers'][0]['sources']
        self.assertEqual(sources['invalid_annotations'], 1)
        self.assertEqual(sources['no_annotations'], 1)
        self.assertEqual(sources['download_failed'], 0)
        self.assertEqual(sources['errors'], 1)

    def test_provider_failure_does_not_skip_the_other_provider_or_expose_credentials(self):
        self.domain.ego4d.ensure_task_annotations.side_effect = RuntimeError('secret signed URL')
        result = self.completed('ambos')
        self.assertEqual(result.status_code, 409)
        self.assertEqual([item['state'] for item in result.json['providers']], ['error', 'ready'])
        self.sources.sync_catalog.assert_called_once()
        self.assertNotIn('secret', result.get_data(as_text=True))
        self.assertTrue(all(provider == 'nymeria' for _, provider, _ in self.calls))

    def test_empty_duration_range_is_distinct_from_a_source_failure(self):
        self.domain._compatible_task_clips = lambda *args, **kwargs: []
        result = self.completed()
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json['code'], 'provider_catalog_empty')
        self.assertEqual(result.json['providers'][0]['state'], 'empty')
        self.assertEqual(result.json['providers'][0]['sources']['state'], 'ready')

    def test_lookup_is_scope_bound_and_does_not_schedule_missing_work(self):
        self.assertIsNone(self.loader.lookup(('absent',), scope='provider-index'))
        self.assertFalse(self.loader.busy)
        self.client.post('/api/catalogs/providers/prepare?dataset=nymeria')
        self.assertTrue(self.entered.wait(1))
        key = next(iter(self.loader._jobs))
        self.assertIsNone(self.loader.lookup(key, scope='campaign-tasks'))
        self.assertEqual(self.loader.lookup(key, scope='provider-index')[1], 202)

    def test_other_operation_admitted_first_prevents_source_acquisition(self):
        guards = [(getattr(self.services, name), 'running') for name in
                  ('RUNNER', 'RECOVERY', 'HOLO_CACHE_RUNNER', 'nymeria_library_worker')]
        guards += [(self.services.campaign_verifications, 'busy'), (self.services.nymeria_catalog, 'busy')]
        for owner, field in guards:
            with self.subTest(field=field, owner=owner):
                setattr(owner, field, True)
                response = self.client.post('/api/catalogs/providers/prepare?dataset=nymeria')
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json['code'], 'provider_catalog_busy')
                self.assertFalse(self.loader.busy)
                setattr(owner, field, False)
        self.services.campaign_drain['requests'] = 1
        self.assertEqual(self.client.post('/api/catalogs/providers/prepare?dataset=nymeria').status_code, 409)
        self.services.campaign_drain.update(requests=0, requested=True)
        response = self.client.post('/api/catalogs/providers/prepare?dataset=nymeria')
        self.assertEqual(response.json['error_code'], 'campaign_closing')
        self.sources.sync_catalog.assert_not_called()
        self.assertEqual(self.calls, [])

    def test_preparation_reservation_is_visible_under_admission_lock_and_app_local(self):
        other_app_loader = CatalogLoader()
        initial = self.client.post('/api/catalogs/providers/prepare?dataset=nymeria')
        self.assertEqual(initial.status_code, 202)
        with self.services._HEAVY_RUNNER_LOCK:
            self.assertTrue(self.loader.busy_for_scope('provider-index'))
            self.assertFalse(self.loader.busy_for_scope('campaign-tasks'))
            self.assertFalse(other_app_loader.busy_for_scope('provider-index'))
        observed = self.client.get('/api/catalogs/providers?dataset=nymeria')
        self.assertEqual(observed.status_code, 202)
        self.assertEqual(observed.json['job_id'], initial.json['job_id'])
        self.release.set()
        deadline = time.monotonic() + 5
        while self.loader.busy and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(self.loader.busy_for_scope('provider-index'))

    def test_saturated_loader_does_not_claim_preparation_was_queued(self):
        entered = threading.Event()
        def occupied(progress):
            entered.set()
            self.release.wait(3)
            return {}, 200
        self.loader.get(('first',), occupied, scope='fixture-first')
        self.assertTrue(entered.wait(1))
        self.loader.get(('second',), occupied, scope='fixture-second')
        response = self.client.post('/api/catalogs/providers/prepare?dataset=nymeria')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['code'], 'provider_catalog_busy')
        self.assertIn('Nenhuma preparação iniciada', response.json['error'])
        self.assertFalse(self.loader.busy_for_scope('provider-index'))
        self.assertEqual(self.client.get('/api/catalogs/providers?dataset=nymeria').json['state'], 'idle')
        self.sources.sync_catalog.assert_not_called()

    def test_http_preparation_blocks_campaign_library_and_accelerator_admission(self):
        from moneymin.web import server
        app = server.create_app(for_testing=True)
        dependencies = app.extensions['feature_dependencies']
        for name in ('task_catalog', 'campaign', 'nymeria_library', '_HEAVY_RUNNER_LOCK',
                     'RUNNER', 'RECOVERY', 'HOLO_CACHE_RUNNER', 'nymeria_library_worker',
                     'campaign_drain', 'campaign_verifications', 'nymeria_catalog'):
            dependencies.provide(name, getattr(self.services, name))
        with patch.object(server, '_list_accounts', side_effect=AssertionError('account access forbidden')), \
             patch.object(server, '_validate_campaign_request', side_effect=AssertionError('validation must wait')):
            client = app.test_client()
            initial = client.post('/api/catalogs/providers/prepare?dataset=nymeria')
            self.assertEqual(initial.status_code, 202, initial.json)
            self.assertTrue(self.entered.wait(1))
            for path in ('/api/campaigns', '/api/campaigns/preflight', '/api/campaigns/original',
                         '/api/campaigns/original/preflight', '/api/recovery/resume'):
                with self.subTest(path=path):
                    response = client.post(path, json={})
                    self.assertEqual(response.status_code, 409, response.json)
                    self.assertEqual(response.json['error_code'], 'library_preparation_busy')
            for path, body in (('/api/library/nymeria/sync', {}),
                               ('/api/library/nymeria/plan', {}),
                               ('/api/library/nymeria/download', {'seq_ids': ['fixture']}),
                               ('/api/holo-cache/start', {'provider': 'holoassist'})):
                with self.subTest(path=path):
                    response = client.post(path, json=body)
                    self.assertEqual(response.status_code, 409, response.json)
            observed = client.get('/api/catalogs/providers?dataset=nymeria')
            self.assertEqual(observed.status_code, 202)
            self.assertEqual(observed.json['job_id'], initial.json['job_id'])
            self.sources.sync_catalog.assert_called_once()


class ProviderSourcePreparationWorkflowTests(unittest.TestCase):
    setUp = library_fixture.NymeriaLibraryTests.setUp
    asset = library_fixture.NymeriaLibraryTests.asset
    archive = library_fixture.NymeriaLibraryTests.archive
    add_sequence = library_fixture.NymeriaLibraryTests.add_sequence
    publish = library_fixture.NymeriaLibraryTests.publish
    respond = library_fixture.NymeriaLibraryTests.respond

    def test_prepare_acquires_annotations_indexes_and_reuses_after_restart(self):
        from flask import jsonify, request
        from moneymin import nymeria_library
        app = Flask(__name__)
        loader = CatalogLoader(timeout_s=300)
        services = SimpleNamespace(campaign=campaign, task_catalog=loader,
            request=request, jsonify=jsonify, nymeria_library=nymeria_library,
            _HEAVY_RUNNER_LOCK=threading.Lock(),
            RUNNER=SimpleNamespace(running=False), RECOVERY=SimpleNamespace(running=False),
            HOLO_CACHE_RUNNER=SimpleNamespace(running=False),
            nymeria_library_worker=SimpleNamespace(running=False),
            campaign_drain={"requested": False, "requests": 0},
            campaign_verifications=SimpleNamespace(busy=False), nymeria_catalog=SimpleNamespace(busy=False),
            campaign_closing_response=lambda: (jsonify({"error_code": "campaign_closing"}), 409),
            config=SimpleNamespace(DATA_DIR=self.root / 'indexes', MEDIA_DATA_DIR=self.root),
            _parse_duration_range=lambda args: (300., 1800.))
        app.add_url_rule('/prepare', endpoint='prepare', methods=['POST'], view_func=lambda:
                         provider_catalogs.prepare_provider_catalog(services))
        app.add_url_rule('/status', endpoint='status', view_func=lambda:
                         provider_catalogs.get_provider_catalog(services))
        nymeria.clear_caches()
        self.addCleanup(nymeria.clear_caches)
        self.assertFalse((self.root / 'sequence_one/narration/atomic_action.csv').exists())
        with patch.dict(os.environ, {'NYMERIA_ROOT': str(self.root)}), \
             patch.object(campaign.task_matching, 'TASK_RULES',
                          {fixture.TASK: campaign.task_matching.TASK_RULES[fixture.TASK]}), \
             patch.object(nymeria, '_snapshot', side_effect=AssertionError('must not open media')):
            with app.test_client() as client:
                initial = client.post('/prepare?dataset=nymeria')
                self.assertEqual(initial.status_code, 202)
                deadline = time.monotonic() + 10
                while loader.busy and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertFalse(loader.busy)
                final = client.get('/status?dataset=nymeria')
                self.assertEqual(final.status_code, 200, final.json)
                self.assertTrue(final.json['ready'])
                self.assertGreater(final.json['providers'][0]['candidates'], 0)
                self.assertTrue((self.root / 'sequence_one/narration/atomic_action.csv').is_file())
                self.assertTrue(list((self.root / '_catalog').glob('task_selection_*.json')))
                self.assertEqual(len(self.calls), 2)
                self.assertTrue(all('metadata' in url or 'narration' in url for url, _ in self.calls))
                self.assertFalse(list(self.root.rglob('*.vrs')))
                nymeria.clear_caches()
                with prepared_catalog_read(), patch.object(nymeria, '_planned_sequence_batch',
                        side_effect=AssertionError('prepared read reclassified')):
                    self.assertTrue(campaign._compatible_task_clips(fixture.TASK, 'nymeria',
                        min_dur_s=300, max_dur_s=1800, catalog_only=True))


def test_queued_catalog_wait_does_not_expire_at_running_idle_deadline():
    loader = CatalogLoader(timeout_s=300)
    row = {'state':'queued', 'started':100., 'last_progress':100., 'message':'queued',
           'phase':'queued', 'job_id':'fixture'}
    assert loader._response(row, 700.)[1] == 202
    assert loader._response(row, 1901.)[1] == 504
    row['state'] = 'running'
    assert loader._response(row, 700.)[1] == 504
