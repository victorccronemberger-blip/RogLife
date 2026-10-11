"""Final atomic workflow phases must belong to the actual bounded cut."""
import unittest
from moneymin import nymeria


class NymeriaWorkflowEndpointTests(unittest.TestCase):
    def test_final_start_caption_is_included_but_sensor_and_duration_limits_hold(self):
        name = 'Using the Dishwasher'
        rows = [(0., 40., 'C loads dirty plates into the dishwasher.'),
                (40., 80., 'C loads dirty dishes into the dishwasher.'),
                (80., 95., 'C starts the dishwasher.')]
        span = {'start':0., 'end':80.}
        self.assertFalse(nymeria.selection_window_complete(name, rows, 0., 80.))
        actual = nymeria.selection_span_window(name, rows, span, 0., 95., 180.)
        self.assertEqual(actual, (0., 95.))
        self.assertTrue(nymeria.selection_window_complete(name, rows, *actual))
        self.assertEqual(nymeria.selection_span_window(name, rows, span, 0., 90., 180.), (0.,80.))
        self.assertEqual(nymeria.selection_span_window(name, rows, span, 0., 95., 90.), (0.,80.))

    def test_unload_then_put_away_is_complete_only_including_final_interval(self):
        rows = [(0., 80., 'C unloads clean plates from the dishwasher.'),
                (80., 95., 'C puts clean plates into the kitchen cabinet.')]
        actual = nymeria.selection_span_window('Using the Dishwasher', rows,
            {'start':0., 'end':80.}, 0., 95., 180.)
        self.assertEqual(actual, (0.,95.))
        self.assertTrue(nymeria.selection_window_complete('Using the Dishwasher', rows, *actual))
        dirty = [(a,b,t.replace('clean', 'dirty')) for a,b,t in rows]
        self.assertFalse(nymeria.selection_window_complete('Using the Dishwasher', dirty, *actual))

    def test_vacuum_requires_wearer_floor_action_and_final_storage(self):
        name = 'Vacuum Floors'
        rows = [(0., 80., 'C vacuums the carpet.'),
                (80., 95., 'C puts the vacuum cleaner into the storage closet.')]
        actual = nymeria.selection_span_window(name, rows, {'start':0., 'end':80.}, 0.,95.,180.)
        self.assertEqual(actual, (0.,95.))
        self.assertTrue(nymeria.selection_window_complete(name, rows, *actual))
        self.assertFalse(nymeria.selection_window_complete(name, rows, 0.,80.))
        self.assertFalse(nymeria.selection_window_complete(name, rows[:1], 0.,95.))
        peer = [rows[0], (80.,95.,'The peer puts the vacuum cleaner into the storage closet.')]
        self.assertFalse(nymeria.selection_window_complete(name, peer, 0.,95.))
        self.assertFalse(nymeria.selection_window_complete(name, list(reversed([
            (0.,15.,rows[1][2]), (15.,95.,rows[0][2])])), 0.,95.))

    def test_extension_does_not_cross_hygiene_or_competing_activity(self):
        for tail in ('C starts the dishwasher while sitting on a chair.',
                     'C folds a shirt with both hands.'):
            with self.subTest(tail=tail):
                rows = [(0.,80.,'C loads dirty plates into the dishwasher.'), (80.,95.,tail)]
                self.assertEqual(nymeria.selection_span_window('Using the Dishwasher', rows,
                    {'start':0.,'end':80.}, 0.,95.,180.), (0.,80.))
