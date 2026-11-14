import importlib
import unittest

from crawlmerge import merge, resume

module = importlib.import_module("crawlmerge.merge")


class MergeTest(unittest.TestCase):
    def setUp(self):
        module._rows.clear()

    def test_merge_adds(self):
        self.assertEqual(merge([[{"id": 1, "v": 1}]])["rows"], 1)

    def test_later_shard_wins(self):
        merge([[{"id": 1, "v": 2}]])
        merge([[{"id": 1, "v": 1}]])
        self.assertEqual(module._rows[1]["v"], 1)

    def test_resume_zero(self):
        self.assertEqual(resume()["done"], 0)
