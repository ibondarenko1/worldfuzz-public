"""Independent benchmark checks. Tiny rule fixtures are not evaluation cases."""
import unittest
from worldfuzz.published_benchmarks import check_route, load_cases


class PublishedChecks(unittest.TestCase):
    def test_original_files_and_selection_are_bound_to_published_rows(self):
        provenance, selection, maps, cases = load_cases()
        self.assertFalse(provenance['modified'])
        self.assertEqual(len(maps), 3)
        self.assertEqual(len(cases), 9)
        self.assertEqual(len({(c['scenario_file'], c['data_row']) for c in cases}), 9)
        for case in cases:
            self.assertEqual((len(maps[case['map']]), len(maps[case['map']][0])), (256, 256))
            self.assertEqual(case['file_line'], case['data_row'] + 1)

    def test_independent_monitor_rejects_corner_cut_even_with_plausible_length(self):
        case = {'width': 2, 'height': 2, 'start': [0, 0], 'goal': [1, 1], 'published_length': 2**.5}
        result = check_route(['.@', '..'], case, [[0, 0], [1, 1]])
        self.assertIn('CORNER_CUT', result['errors'])
        self.assertFalse(result['matches_published_optimum'])

    def test_legal_but_longer_route_does_not_pass_published_reference(self):
        case = {'width': 3, 'height': 2, 'start': [0, 0], 'goal': [2, 0], 'published_length': 2}
        longer = check_route(['...', '...'], case, [[0, 0], [0, 1], [1, 1], [2, 1], [2, 0]])
        self.assertTrue(longer['route_valid'])
        self.assertEqual(longer['verdict'], 'LENGTH_MISMATCH')
        valid = check_route(['...', '...'], case, [[0, 0], [1, 0], [2, 0]])
        self.assertEqual(valid['verdict'], 'MATCH')

    def test_wrong_goal_blocked_cell_and_non_neighbour_are_rejected(self):
        case = {'width': 3, 'height': 2, 'start': [0, 0], 'goal': [2, 0], 'published_length': 2}
        for route, error in [([[0, 0]], 'ENDPOINT_MISMATCH'), ([[0, 0], [1, 0], [2, 0]], 'BLOCKED_CELL'), ([[0, 0], [2, 0]], 'INVALID_STEP')]:
            result = check_route(['.@.', '...'], case, route)
            self.assertIn(error, result['errors'])
