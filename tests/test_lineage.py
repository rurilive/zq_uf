import unittest

from filelineage import impact, record


class LineageTest(unittest.TestCase):
    def test_record_counts_reads(self):
        self.assertEqual(record({"id": "j1", "reads": ["a.csv"], "writes": ["b.csv"]})["reads"], 1)

    def test_record_builds_no_edges(self):
        self.assertEqual(record({"id": "j2", "reads": ["c.csv"], "writes": []})["edges"], 0)

    def test_impact_is_one_hop(self):
        record({"id": "j3", "reads": ["d.csv"], "writes": ["e.csv"]})
        self.assertEqual(impact("d.csv")["depth"], 1)
