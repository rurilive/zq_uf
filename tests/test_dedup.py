import unittest

from metricdedup import flush, put
from metricdedup import dedup


class DedupTest(unittest.TestCase):
    def setUp(self):
        dedup._seen.clear(); dedup._written = 0; dedup._dup = 0

    def test_first_written(self):
        self.assertTrue(put({"name": "cpu", "ts": 1})["written"])

    def test_duplicate(self):
        put({"name": "cpu", "ts": 1})
        self.assertTrue(put({"name": "cpu", "ts": 1})["duplicate"])

    def test_flush_counts(self):
        put({"name": "cpu", "ts": 1}); put({"name": "cpu", "ts": 1})
        self.assertEqual(flush()["duplicate"], 1)
