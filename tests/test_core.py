import unittest

from cdcmerge import Merger


class CdcmergeTest(unittest.TestCase):
    def test_insert_and_update(self):
        merger = Merger()
        merger.apply([{"op": "insert", "key": "a", "value": 1}])
        merger.apply([{"op": "update", "key": "a", "value": 2}])
        self.assertEqual(merger.snapshot(), {"a": 2})

    def test_delete(self):
        merger = Merger()
        merger.apply([{"op": "insert", "key": "a", "value": 1}])
        merger.apply([{"op": "delete", "key": "a"}])
        self.assertEqual(merger.snapshot(), {})

    def test_new_merger_is_empty(self):
        self.assertEqual(Merger().snapshot(), {})
