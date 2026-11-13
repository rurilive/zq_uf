import unittest

from lineage import Lineage


class LineageTest(unittest.TestCase):
    def test_record(self):
        lineage = Lineage()
        item = lineage.record({"name": "job1", "inputs": ["raw"], "outputs": ["clean"]})
        self.assertEqual(item["outputs"], ["clean"])

    def test_impact_direct(self):
        lineage = Lineage()
        lineage.record({"name": "job1", "inputs": ["raw"], "outputs": ["clean"]})
        self.assertEqual(lineage.impact("raw"), ["job1"])

    def test_impact_unknown(self):
        self.assertEqual(Lineage().impact("nope"), [])
