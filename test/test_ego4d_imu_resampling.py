from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from moneymin import ego4d


def _write_imu(path: Path, *, accel_gap: bool = False) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow([
            "component_idx", "component_timestamp_ms", "canonical_timestamp_ms",
            "gyro_x", "gyro_y", "gyro_z", "accl_x", "accl_y", "accl_z",
        ])
        for timestamp in range(0, 1001, 10):
            accel_present = timestamp % 20 == 0
            if accel_gap and 100 < timestamp < 900:
                accel_present = False
            accel = ("1", "2", "3") if accel_present else ("", "", "")
            writer.writerow([
                0, timestamp, timestamp, "0.1", "0.2", "0.3", *accel,
            ])


class Ego4dImuResamplingTests(unittest.TestCase):
    def test_sensor_output_cannot_extend_the_original_video_window(self) -> None:
        with self.assertRaisesRegex(ValueError, 'ultrapassa'):
            ego4d.build_imu_csv('not-opened.csv', (0, 1), duration_ms=2000)

    def test_account_seed_does_not_change_measured_sensors(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "imu.csv"
            _write_imu(source)
            original = ego4d.build_imu_csv(source, (0, 1), duration_ms=1000)
            for seed in ("account-a", "account-b"):
                self.assertEqual(original, ego4d.build_imu_csv(
                    source, (0, 1), duration_ms=1000, seed=seed))

    def test_resamples_sensors_with_different_native_rates(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "imu.csv"
            _write_imu(source)

            result = ego4d.build_imu_csv(
                source, (0.0, 1.0), duration_ms=1000)
            rows = result.strip().splitlines()

        self.assertEqual(rows[0], "t,ax,ay,az,wx,wy,wz")
        self.assertEqual(
            len(rows), 502)  # cabeçalho + 500 Hz inclusivo em 1 segundo
        self.assertEqual(
            rows[1],
            "0,1.000000,2.000000,3.000000,0.100000,0.200000,0.300000",
        )

    def test_rejects_a_real_gap_in_either_sensor(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "imu-gap.csv"
            _write_imu(source, accel_gap=True)

            with self.assertRaisesRegex(
                    RuntimeError, "acelerômetro.*lacuna máxima"):
                ego4d.build_imu_csv(
                    source, (0.0, 1.0), duration_ms=1000,
                    validate_only=True)

    def test_continuity_gate_uses_exact_source_gaps_at_target_rates(self) -> None:
        for sample_rate_hz in (20, 40, 100, 500, 1000):
            for phase_ms in (0.0, 0.75, 1.9):
                for gap_ms in (25, 26, 29, 30):
                    with self.subTest(sample_rate_hz=sample_rate_hz,
                                      phase_ms=phase_ms, gap_ms=gap_ms), \
                        tempfile.TemporaryDirectory() as folder:
                        source = Path(folder) / "imu.csv"
                        end_ms = int(phase_ms + gap_ms + 2)
                        with source.open("w", encoding="utf-8", newline="") as stream:
                            writer = csv.writer(stream, lineterminator="\n")
                            writer.writerow([
                                "canonical_timestamp_ms", "gyro_x", "gyro_y", "gyro_z",
                                "accl_x", "accl_y", "accl_z",
                            ])
                            for timestamp in (phase_ms, phase_ms + gap_ms):
                                writer.writerow([timestamp, 1, 2, 3, 4, 5, 6])

                        call = lambda: ego4d.build_imu_csv(
                            source, (0.0, end_ms / 1000),
                            sample_rate_hz=sample_rate_hz, duration_ms=end_ms,
                            validate_only=True)
                        if gap_ms == 25:
                            call()
                        else:
                            with self.assertRaisesRegex(RuntimeError, "cobertura IMU insuficiente"):
                                call()

    def test_continuity_gate_measures_real_window_edges(self) -> None:
        for edge in ("leading", "trailing"):
            for gap_ms in (25, 26):
                with self.subTest(edge=edge, gap_ms=gap_ms), \
                        tempfile.TemporaryDirectory() as folder:
                    source = Path(folder) / "imu.csv"
                    timestamps = ((gap_ms, 40) if edge == "leading"
                                  else (0, 40 - gap_ms))
                    with source.open("w", encoding="utf-8", newline="") as stream:
                        writer = csv.writer(stream, lineterminator="\n")
                        writer.writerow([
                            "canonical_timestamp_ms", "gyro_x", "gyro_y", "gyro_z",
                            "accl_x", "accl_y", "accl_z",
                        ])
                        for timestamp in timestamps:
                            writer.writerow([timestamp, 1, 2, 3, 4, 5, 6])

                    call = lambda: ego4d.build_imu_csv(
                        source, (0.0, 0.04), duration_ms=40, validate_only=True)
                    if gap_ms == 25:
                        call()
                    else:
                        with self.assertRaisesRegex(RuntimeError, "cobertura IMU insuficiente"):
                            call()

    def test_gap_error_reports_the_measured_source_rate(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "imu.csv"
            with source.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, lineterminator="\n")
                writer.writerow([
                    "canonical_timestamp_ms", "gyro_x", "gyro_y", "gyro_z",
                    "accl_x", "accl_y", "accl_z",
                ])
                for timestamp in range(0, 10_001, 5):
                    if 5_005 <= timestamp <= 5_045:
                        continue
                    writer.writerow([timestamp, 1, 2, 3, 4, 5, 6])

            with self.assertRaisesRegex(
                    RuntimeError, r"taxa efetiva=199\.2Hz.*lacuna máxima=50\.000ms"):
                ego4d.build_imu_csv(
                    source, (0.0, 10.0), duration_ms=10_000,
                    validate_only=True)
