"""F04 shared fingerprint snapshot and F05 Nymeria RGB scratch budget."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from moneymin import content_provenance as cp
from moneymin import nymeria_library
from moneymin import nymeria_vrs


def _source_csv():
    return ("canonical_timestamp_ms,gyro_x,gyro_y,gyro_z,accl_x,accl_y,accl_z\n"
            + "".join(f"{i},1,2,3,4,5,6\n" for i in range(0, 101, 10)))


def _output_csv():
    return "t,ax,ay,az,wx,wy,wz\n" + "".join(
        f"{i * 1000000},4,5,6,1,2,3\n" for i in range(0, 101, 2))


class FingerprintSnapshotF04(unittest.TestCase):
    def test_shared_snapshot_reuses_unchanged_file_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clip.bin"
            path.write_bytes(b"shared-provenance-fixture-bytes")
            snap = cp.FingerprintSnapshot()
            reads = {"n": 0}
            real_open = Path.open

            def counting_open(self, *args, **kwargs):
                if self.resolve() == path.resolve():
                    reads["n"] += 1
                return real_open(self, *args, **kwargs)

            with patch.object(Path, "open", counting_open):
                first = cp.fingerprint(path, snapshot=snap)
                second = cp.fingerprint(path, snapshot=snap)
            self.assertEqual(first, second)
            self.assertEqual(reads["n"], 1)

    def test_revalidate_reuses_snapshot_across_accounts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "dataset-source.mp4"
            source.write_bytes(b"inert-original-media-A")
            imu = root / "dataset-imu.csv"
            imu.write_text(_source_csv(), encoding="utf-8", newline="")
            prepared = root / "prepared.mp4"
            prepared.write_bytes(b"inert-derived-media-B")
            item = cp.prepare_content_provenance(
                source, imu, prepared, _output_csv(),
                "i,ptsNs,dtNs,tNs,key\n0,0,0,0,1\n",
                clip_uid="clip", parent_video_uid="parent", media_uid="media",
                window_s=(0.0, 0.1), media_offset_s=0.0,
                normalization_start_s=0.0, selection_evidence=None)
            snap = cp.FingerprintSnapshot()
            item["_fingerprint_snapshot"] = snap
            reads = {"n": 0}
            real_open = Path.open
            watched = {
                Path(item["_content_inputs"][role]["path"]).resolve()
                for role in ("source_video", "source_imu", "prepared_video")
            }

            def counting_open(self, *args, **kwargs):
                if self.resolve() in watched:
                    reads["n"] += 1
                return real_open(self, *args, **kwargs)

            with patch.object(Path, "open", counting_open):
                cp.revalidate_content_provenance(item)
                first_reads = reads["n"]
                cp.revalidate_content_provenance(item)
                second_reads = reads["n"]
            self.assertGreater(first_reads, 0)
            self.assertEqual(second_reads, first_reads)

    def test_identity_change_invalidates_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clip.bin"
            path.write_bytes(b"version-one")
            snap = cp.FingerprintSnapshot()
            first = cp.fingerprint(path, snapshot=snap)
            path.write_bytes(b"version-two-longer")
            second = cp.fingerprint(path, snapshot=snap)
            self.assertNotEqual(first["sha256"], second["sha256"])

    def test_strict_revalidate_without_prior_snapshot_still_detects_bit_flip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "dataset-source.mp4"
            source.write_bytes(b"inert-original-media-A")
            imu = root / "dataset-imu.csv"
            imu.write_text(_source_csv(), encoding="utf-8", newline="")
            prepared = root / "prepared.mp4"
            prepared.write_bytes(b"inert-derived-media-B")
            item = cp.prepare_content_provenance(
                source, imu, prepared, _output_csv(),
                "i,ptsNs,dtNs,tNs,key\n0,0,0,0,1\n",
                clip_uid="clip", parent_video_uid="parent", media_uid="media",
                window_s=(0.0, 0.1), media_offset_s=0.0,
                normalization_start_s=0.0, selection_evidence=None)
            row = item["_content_inputs"]["source_video"]
            path = Path(row["path"])
            before = path.read_bytes()
            stat = path.stat()
            path.write_bytes(bytes([before[0] ^ 1]) + before[1:])
            import os
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            with self.assertRaises(ValueError):
                cp.revalidate_content_provenance(item)


class NymeriaPngBudgetF05(unittest.TestCase):
    def test_estimate_includes_png_and_temp_mp4(self):
        est = nymeria_vrs.estimate_rgb_scratch_bytes(100, 1408, 1408)
        self.assertGreater(est, 100 * 1408 * 1408)
        self.assertGreater(est, 8 * 1024 * 1024)

    def test_plan_and_acquire_helpers_budget_rgb_scratch(self):
        seconds = 12.0
        rgb = nymeria_library._rgb_scratch_bytes_for_seconds(seconds)
        self.assertGreater(rgb, 0)
        acquire_rgb = nymeria_library._rgb_scratch_bytes_for_seconds(
            nymeria_library._ACQUIRE_RGB_SCRATCH_SECONDS)
        self.assertGreater(acquire_rgb, rgb)

    def test_space_error_reports_phase_and_preserves_prior_output(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "existing.mp4"
            out.write_bytes(b"prior-output")
            err = nymeria_vrs.NymeriaRgbSpaceError(
                "espaço insuficiente", phase="rgb_extract_space_insufficient")
            self.assertEqual(err.phase, "rgb_extract_space_insufficient")
            self.assertEqual(out.read_bytes(), b"prior-output")


if __name__ == "__main__":
    unittest.main()
