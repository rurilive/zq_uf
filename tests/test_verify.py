import unittest

from crawlverify import rollback, verify


class VerifyTest(unittest.TestCase):
    def test_missing_field_isolated(self):
        result = verify([{"id": 1}], {"required": ["id", "title"]})
        self.assertEqual(result["isolated"][0]["reason"], "missing")

    def test_complete_row_passes(self):
        result = verify([{"id": 1, "title": "x"}], {"required": ["id", "title"]})
        self.assertEqual(result["passed"], [0])

    def test_rollback_is_whole_batch(self):
        self.assertEqual(rollback("b1")["rolled_back"], "all")
