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
            for phase_ms in (0.0, 0.25, 0.5):
                for gap_ms in (250, 251, 260):
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
                        if gap_ms == 250:
                            call()
                        else:
                            with self.assertRaisesRegex(RuntimeError, "cobertura IMU insuficiente"):
                                call()

    def test_continuity_gate_measures_real_window_edges(self) -> None:
        for edge in ("leading", "trailing"):
            for gap_ms in (250, 251):
                with self.subTest(edge=edge, gap_ms=gap_ms), \
                        tempfile.TemporaryDirectory() as folder:
                    source = Path(folder) / "imu.csv"
                    timestamps = ((gap_ms, gap_ms + 40) if edge == "leading"
                                  else (0, 40))
                    with source.open("w", encoding="utf-8", newline="") as stream:
                        writer = csv.writer(stream, lineterminator="\n")
                        writer.writerow([
                            "canonical_timestamp_ms", "gyro_x", "gyro_y", "gyro_z",
                            "accl_x", "accl_y", "accl_z",
                        ])
                        for timestamp in timestamps:
                            writer.writerow([timestamp, 1, 2, 3, 4, 5, 6])

                    duration_ms = gap_ms + 40
                    call = lambda: ego4d.build_imu_csv(
                        source, (0.0, duration_ms / 1000), duration_ms=duration_ms,
                        validate_only=True)
                    if gap_ms == 250:
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
                    if 5_005 <= timestamp <= 5_255:
                        continue
                    writer.writerow([timestamp, 1, 2, 3, 4, 5, 6])

            with self.assertRaisesRegex(
                    RuntimeError, r"taxa efetiva=195\.0Hz.*lacuna máxima=260\.000ms"):
                ego4d.build_imu_csv(
                    source, (0.0, 10.0), duration_ms=10_000,
                    validate_only=True)

    def test_admission_admits_a_105ms_real_world_gap(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "imu-105ms-gap.csv"
            with source.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, lineterminator="\n")
                writer.writerow([
                    "canonical_timestamp_ms", "gyro_x", "gyro_y", "gyro_z",
                    "accl_x", "accl_y", "accl_z",
                ])
                for timestamp in range(0, 30_001, 5):
                    if 10_005 <= timestamp <= 10_105:
                        continue
                    writer.writerow([timestamp, 1, 2, 3, 4, 5, 6])
            stats: dict = {}
            result = ego4d.build_imu_csv(
                source, (0.0, 30.0), duration_ms=30_000, stats=stats)
        self.assertTrue(result.startswith("t,ax,ay,az,wx,wy,wz"))
        # O contrato do Minute continua declarando 25 ms; o vão medido real
        # (não inventado) aparece no campo separado de diagnóstico.
        self.assertEqual(stats["maxInterpolationSpanNs"], "25000000")
        self.assertEqual(stats["measuredMaxInterpolationSpanNs"], "110000000")
        self.assertGreater(stats["interpolatedCount"], 0)

    def test_unreadable_imu_csv_is_an_imu_failure_not_a_parse_crash(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "imu-corrupt.csv"
            source.write_bytes(
                b"canonical_timestamp_ms,gyro_x,gyro_y,gyro_z,accl_x,accl_y,accl_z\n"
                b"0,1,2,3,4,5,6\n"
                b"10,1,2,3,4,5,\xff\xfe\n")
            with self.assertRaisesRegex(RuntimeError, "cobertura IMU insuficiente.*ilegível"):
                ego4d.build_imu_csv(source, (0.0, 1.0), duration_ms=1000,
                                    validate_only=True)

    def test_oversized_imu_field_is_not_a_fatal_parse_error(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "imu-oversized.csv"
            blob = b"x" * (200 * 1024)
            with source.open("wb") as stream:
                stream.write(
                    b"canonical_timestamp_ms,gyro_x,gyro_y,gyro_z,accl_x,accl_y,accl_z\n")
                stream.write(b"0,1,2,3,4,5,6\n")
                stream.write(b'10,1,2,3,4,5,"' + blob + b'"\n')
                stream.write(b"20,1,2,3,4,5,6\n")
            result = ego4d.build_imu_csv(source, (0.0, 0.03), duration_ms=30,
                                         validate_only=True)
            self.assertEqual(result, "")
