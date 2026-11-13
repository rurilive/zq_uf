import unittest

from pipelineview import build, impact


JOBS = [
    {"name": "extract"},
    {"name": "clean", "depends": ["extract"]},
    {"name": "report", "depends": ["clean"]},
]


class ViewTest(unittest.TestCase):
    def setUp(self):
        build(JOBS)

    def test_task_level_nodes(self):
        self.assertEqual(build(JOBS)["nodes"], ["extract", "clean", "report"])

    def test_direct_dependents_only(self):
        self.assertEqual(impact("extract"), ["clean"])

    def test_no_field_nodes(self):
        self.assertTrue(all(isinstance(node, str) for node in build(JOBS)["nodes"]))
