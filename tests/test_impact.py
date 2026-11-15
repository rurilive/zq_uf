import unittest

from lineageimpact2 import impact, paths


class ImpactTest(unittest.TestCase):
    def test_one_hop(self):
        self.assertEqual(impact("a")["depth"], 1)

    def test_field_ignored(self):
        self.assertEqual(impact("a", "id")["downstream"], [])

    def test_paths_empty(self):
        self.assertEqual(paths("x")["paths"], [])
