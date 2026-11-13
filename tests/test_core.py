import unittest

from etlconf import transform


class EtlconfTest(unittest.TestCase):
    def test_rename(self):
        rows = [{"old": 1}]
        self.assertEqual(transform(rows, {"rename": {"old": "new"}}), [{"new": 1}])

    def test_unmapped_kept(self):
        rows = [{"a": 1, "b": 2}]
        self.assertEqual(transform(rows, {"rename": {"a": "x"}}), [{"x": 1, "b": 2}])

    def test_empty(self):
        self.assertEqual(transform([], {"rename": {}}), [])
