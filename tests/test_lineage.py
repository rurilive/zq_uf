import unittest

from tablelineage2 import record, remove


class LineageTest(unittest.TestCase):
    def test_record_counts_edges(self):
        job = {"id": "j1", "edges": [{"src": "a", "dst": "b"}, {"src": "b", "dst": "c"}]}
        self.assertEqual(record(job)["edges"], 2)

    def test_record_rebuilds_every_time(self):
        record({"id": "j1", "edges": [{"src": "a", "dst": "b"}]})
        job = {"id": "j2", "edges": [{"src": "c", "dst": "d"}]}
        self.assertEqual(record(job)["edges"], 1)

    def test_remove_keeps_edges(self):
        record({"id": "j1", "edges": [{"src": "a", "dst": "b"}]})
        self.assertEqual(remove({"id": "j1"})["removed"], 0)
