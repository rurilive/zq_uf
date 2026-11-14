import importlib
import unittest

from lineageimpact import impact, paths

module = importlib.import_module("lineageimpact.impact")


class ImpactTest(unittest.TestCase):
    def setUp(self):
        module._edges.clear()
        module.record("raw", "dwd")

    def test_direct_downstream(self):
        self.assertEqual(impact("raw"), ["dwd"])

    def test_field_ignored(self):
        self.assertEqual(impact("raw", "id"), impact("raw"))

    def test_paths_single(self):
        self.assertEqual(paths("raw"), ["raw"])
