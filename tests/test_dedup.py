import unittest

from metricdedup2 import flush, put


class DedupTest(unittest.TestCase):
    def test_same_key_overwrites(self):
        put({"key": "a", "version": 1, "value": 10})
        before = put({"key": "a", "version": 2, "value": 20})["size"]
        self.assertEqual(put({"key": "a", "version": 3, "value": 30})["size"], before)

    def test_put_drops_nothing(self):
        self.assertEqual(put({"key": "b", "version": 1})["dropped"], 0)

    def test_flush_keeps_state(self):
        put({"key": "c", "version": 1})
        self.assertEqual(flush()["expired"], 0)
