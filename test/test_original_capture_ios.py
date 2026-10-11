"""Inert iOS-shaped fixtures: test compatibility, never physical acquisition."""
from pathlib import Path
import copy
import tempfile
import unittest
from unittest.mock import patch
from moneymin import original_capture, validate
from test_original_capture_workflow import fixture_pair, fixture_probe, EPOCH, OWNER, ORG, TASK


def ios_metadata(meta):
    meta['platform'] = {'os': 'ios', 'version': '27.0.1'}
    meta['device'] = {'model': 'DECLARED INERT IOS FIXTURE', 'systemName': 'iOS', 'systemVersion': '27.0.1'}
    meta['timebase']['clockDomain'] = 'ios_systemUptimeNs'
    meta['cameras'] = [{'name': 'ultra-wide', 'intrinsics_omitted_reason': 'Declared inert fixture; no calibration'}]


class OriginalIosCaptureTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='qfar-ios-fixture-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def plan(self, pairs):
        return original_capture.prepare_original_capture_plan(
            pairs, account_email=OWNER, org_key=ORG, task_id=TASK, now=EPOCH,
            probe=fixture_probe, limits={'min_duration_ms': 1000, 'max_duration_ms': 120000,
                                       'backlog_cap_ms': 600000})

    def test_original_ios_single_and_multichunk_keep_exact_files_and_metadata(self):
        for count in (1, 2):
            with self.subTest(count=count):
                pairs = [fixture_pair(self.root / str(count), i, count=count, change=ios_metadata)
                         for i in range(count)]
                before = [p.read_bytes() for pair in pairs for p in pair]
                plan = self.plan(pairs)
                self.assertEqual(len(plan.captures), count)
                for capture in plan.captures:
                    self.assertEqual(capture.metadata['platform'], {'os': 'ios', 'version': '27.0.1'})
                    self.assertEqual(capture.metadata['timebase']['clockDomain'], 'ios_systemUptimeNs')
                    self.assertNotIn('source', capture.metadata['cameras'][0])
                    self.assertFalse(plan.physical_provenance_verified)
                self.assertEqual([p.read_bytes() for pair in pairs for p in pair], before)

    def test_ios_does_not_accept_android_clock_device_or_invalid_camera_origin(self):
        changes = (lambda m: m['timebase'].update(clockDomain='android_elapsedRealtimeNanos'),
                   lambda m: m['timebase'].update(clockDomain='trinet_camera_monotonic'),
                   lambda m: m['device'].update(systemName='Android'),
                   lambda m: m['cameras'][0].update(source='ego'),
                   lambda m: m['cameras'][0].update(source=None))
        for i, change in enumerate(changes):
            with self.subTest(i=i):
                def modify(meta):
                    ios_metadata(meta)
                    change(meta)
                pairs = [fixture_pair(self.root / f'invalid-{i}', change=modify)]
                with self.assertRaises(original_capture.OriginalCaptureError):
                    self.plan(pairs)

    def test_ios_camera_name_does_not_supply_or_authorize_a_missing_source(self):
        for name in ('ultra-wide', 'wide', 'telephoto'):
            with self.subTest(name=name):
                def modify(meta):
                    ios_metadata(meta)
                    meta['cameras'][0]['name'] = name
                plan = self.plan([fixture_pair(self.root / name, change=modify)])
                self.assertNotIn('source', plan.captures[0].metadata['cameras'][0])
                self.assertFalse(plan.physical_provenance_verified)

    def test_ios_short_post_accepts_source_platform_without_invented_version(self):
        meta = {'logId': 'declared_0', 'durationMs': 60000,
                'platform': {'os': 'ios'}, 'device': {'model': 'DECLARED INERT IOS FIXTURE'}}
        result = validate.summarize(validate.validate_upload_meta(meta, log_id='declared_0', duration_ms=60000))
        self.assertEqual(result['counts']['fail'], 0)
        self.assertEqual(meta['platform'], {'os': 'ios'})

    def test_apple_calibration_lookup_is_preserved_without_android_coefficients(self):
        cameras = [{'name': 'ultra-wide', 'intrinsics': {
            'fx': 578.9, 'fy': 578.9, 'cx': 719.5, 'cy': 538.5, 'coordinate_frame': 'video_frame',
            'intrinsics_reference_dimensions': {'width': 1440, 'height': 1080},
            'distortion_model': 'apple_lens_distortion_lookup_table_v1',
            'lens_distortion_reference_dimensions': {'width': 4032, 'height': 3024},
            'lens_distortion_center': {'x': 2014.0, 'y': 1511.0},
            'lens_distortion_lookup_table': [0.0, 0.01],
            'inverse_lens_distortion_lookup_table': [0.0, -0.01]}}]
        before = copy.deepcopy(cameras)
        self.assertEqual(validate._check_cameras(cameras, platform_os='ios').status, 'pass')
        self.assertEqual(validate._check_cameras(cameras).status, 'fail')
        self.assertEqual(cameras, before)
        for key, value in (('lens_distortion_lookup_table', []),
                           ('inverse_lens_distortion_lookup_table', [float('nan')]),
                           ('lens_distortion_center', {'x': True, 'y': 0}),
                           ('lens_distortion_reference_dimensions', {'width': 0, 'height': 3024})):
            invalid = copy.deepcopy(cameras)
            invalid[0]['intrinsics'][key] = value
            with self.subTest(key=key):
                self.assertEqual(validate._check_cameras(invalid, platform_os='ios').status, 'fail')

    def test_ios_imu_uses_observed_period_but_rejects_relative_or_unbounded_offset(self):
        anchor = 9_007_199_254_740_999
        base = {'clockDomain': 'ios_systemUptimeNs', 'startNs': str(anchor),
                'endNs': str(anchor + 60_000_000_000), 'firstFrameSensorTimestampNs': str(anchor)}
        def csv(first, second):
            return f't,ax,ay,az,wx,wy,wz\n{first},0,0,0,0,0,0\n{second},0,0,0,0,0,0\n'
        data = csv(anchor + 10_000_000, anchor + 20_000_000)
        rate = next(c for c in validate._check_imu_csv(data, 10, platform_os='ios') if c.name == 'imu.rate')
        self.assertEqual(rate.status, 'warn')
        self.assertNotIn('500', rate.detail)
        self.assertEqual(validate._check_imu_timebase(data, base).status, 'pass')
        self.assertEqual(validate._check_imu_timebase(data, {**base, 'clockDomain': 'android_elapsedRealtimeNanos'}).status, 'fail')
        for first, second in ((0, 10_000_000), (anchor + 1_000_000_000, anchor + 2_000_000_000),
                              (anchor, anchor), (anchor, anchor - 1)):
            with self.subTest(first=first, second=second):
                self.assertEqual(validate._check_imu_timebase(csv(first, second), base).status, 'fail')

    def test_ios_capture_validation_is_independent_of_android_nominal_rate(self):
        pairs = [fixture_pair(self.root / 'no-android-rate', change=ios_metadata)]
        before = [path.read_bytes() for pair in pairs for path in pair]
        # No Android rate is available in an iOS-only acquisition adapter.
        # Exercise the complete ZIP/CSV/timebase validation, not just a helper.
        with patch.object(validate.config, 'ANDROID_IMU_SAMPLE_RATE_HZ', None):
            plan = self.plan(pairs)
        self.assertEqual(plan.captures[0].metadata['platform']['os'], 'ios')
        self.assertEqual([path.read_bytes() for pair in pairs for path in pair], before)
        self.assertFalse(plan.physical_provenance_verified)

