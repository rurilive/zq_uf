import unittest

from latejoin import join


class LatejoinTest(unittest.TestCase):
    def test_join_latest(self):
        facts = [{"id": 1, "v": 10}]
        dims = [{"id": 1, "name": "old"}, {"id": 1, "name": "new"}]
        self.assertEqual(join(facts, dims)[0]["dim"]["name"], "new")

    def test_missing_dim(self):
        self.assertIsNone(join([{"id": 2}], [])[0]["dim"])
