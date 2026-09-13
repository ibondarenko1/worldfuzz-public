"""Task catalog validity and immutable defaults. No simulator execution."""
import unittest
from worldfuzz.navigation_tasks import task_catalog
from worldfuzz.regression import validate_project


class TaskCatalogTests(unittest.TestCase):
    def test_tasks_are_distinct_bounded_projects_without_implicit_execution(self):
        tasks = task_catalog()
        self.assertEqual(len({t['id'] for t in tasks}), 3)
        self.assertEqual(len({str(t['project']['scenario']) for t in tasks}), 3)
        for task in tasks:
            project = validate_project(task['project'])
            self.assertEqual(project['search']['budget'], 1)
            self.assertEqual(project['reduction']['budget'], 0)
            self.assertEqual(project['search']['position_jitter_m'], 0)

    def test_editing_or_reducing_one_task_does_not_change_another_load(self):
        tasks = task_catalog()
        original = task_catalog()
        tasks[0]['project']['scenario']['obstacles'].clear()
        tasks[1]['project']['candidate']['parameters']['max_speed_mps'] = .2
        self.assertEqual(task_catalog(), original)
