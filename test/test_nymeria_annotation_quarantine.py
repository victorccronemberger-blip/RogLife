"""Recover unaffected annotations without borrowing uncertain action intervals."""
import csv
import hashlib
from pathlib import Path
import tempfile
import unittest

from moneymin import nymeria, nymeria_library

ACTION = "C is folding a shirt with both hands while standing in the living room."
TASK = "Folding Clothes or Putting Them on Hangers"


class NymeriaAnnotationQuarantineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.seq = Path(self.temp.name)
        (self.seq / 'narration').mkdir()
        self.path = self.seq / 'narration/atomic_action.csv'

    def write(self, rows):
        with self.path.open('w', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(('start_time', 'end_time', 'Describe my atomic actions'))
            writer.writerows(rows)

    def test_reversed_interval_is_quarantined_without_discarding_unaffected_rows(self):
        self.write([(0, 10, ACTION), (20, 30, ACTION), (40, 50, ACTION),
                    (30, 20, 'C waters the garden plants.')])
        original = self.path.read_bytes()
        stats = {}
        rows, hashes = nymeria._annotation_rows(self.seq, stats=stats)
        self.assertEqual(rows, [(0., 10., ACTION), (40., 50., ACTION)])
        self.assertEqual(stats['invalid_rows'], 1)
        self.assertEqual(stats['quarantined_regions'], [[20., 30.]])
        self.assertEqual(stats['removed_overlapping_rows'], 1)
        self.assertEqual(hashes['atomic_action.csv'], hashlib.sha256(original).hexdigest())
        self.assertEqual(self.path.read_bytes(), original)

    def test_nested_overlapping_and_zero_length_regions_exclude_all_touching_captions(self):
        self.write([(0, 10, ACTION), (10, 20, ACTION), (20, 30, ACTION),
                    (30, 40, ACTION), (50, 60, ACTION), (80, 90, ACTION),
                    (35, 15, 'bad'), (25, 20, 'bad'), (60, 60, 'bad')])
        stats = {}
        rows, _ = nymeria._annotation_rows(self.seq, stats=stats)
        self.assertEqual(rows, [(0., 10., ACTION), (80., 90., ACTION)])
        self.assertEqual(stats['quarantined_regions'], [[15., 35.], [60., 60.]])

    def test_unknown_negative_or_nonfinite_times_still_reject_the_recording(self):
        for left, right in [(-1, 0), (float('nan'), 10), (0, float('inf')), ('unknown', 5)]:
            with self.subTest(left=left, right=right):
                self.write([(0, 10, ACTION), (left, right, 'bad')])
                with self.assertRaises(ValueError):
                    nymeria._annotation_rows(self.seq)

    def test_missing_caption_is_not_used_as_action_evidence(self):
        self.write([(0, 10, ACTION), (20, 30, ACTION), (40, 50, ACTION), (25, 35, ' ')])
        stats = {}
        rows, _ = nymeria._annotation_rows(self.seq, stats=stats)
        self.assertEqual(rows, [(0., 10., ACTION), (40., 50., ACTION)])
        self.assertEqual(stats['invalid_rows'], 1)

    def test_an_uncertain_interval_cannot_be_bridged_into_a_qualifying_core(self):
        rows = [(start, start + 5, ACTION) for start in range(0, 125, 5)]
        self.write(rows)
        good, _ = nymeria._annotation_rows(self.seq)
        name = TASK
        self.assertTrue(nymeria_library._catalog_windows(good, [name], 60, 1800))
        self.write([*rows, (75, 45, ACTION)])
        safe, _ = nymeria._annotation_rows(self.seq)
        self.assertTrue(safe)
        self.assertFalse(nymeria_library._catalog_windows(safe, [name], 60, 1800))

    def test_an_unaffected_complete_core_is_retained_after_a_bad_region(self):
        rows = [(start, start + 5, ACTION) for start in range(0, 305, 5)]
        self.write([*rows, (75, 45, ACTION)])
        safe, _ = nymeria._annotation_rows(self.seq)
        windows = nymeria_library._catalog_windows(safe, [TASK], 60, 1800)
        self.assertTrue(windows)
        self.assertTrue(all(window['device_seconds'][0] > 75 for window in windows))


if __name__ == '__main__':
    unittest.main()
