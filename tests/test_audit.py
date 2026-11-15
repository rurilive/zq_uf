import unittest

from tableaudit import query, record


class AuditTest(unittest.TestCase):
    def test_record_grows(self):
        record({"table": "t1", "op": "select"})
        self.assertGreaterEqual(record({"table": "t1", "op": "select"})["total"], 2)

    def test_record_is_not_aggregated(self):
        self.assertFalse(record({"table": "t2", "op": "drop"})["aggregated"])

    def test_query_matches_exact(self):
        record({"table": "t3", "op": "insert"})
        self.assertEqual(len(query({"table": "t3"})["hits"]), 1)
