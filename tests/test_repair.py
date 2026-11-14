import unittest

from lineagerepair import confirm, infer


class RepairTest(unittest.TestCase):
    def test_infer_returns_empty(self):
        self.assertEqual(infer([{"job": "j1", "read": "a", "write": "b"}]), [])

    def test_confirm_returns_edge(self):
        self.assertEqual(confirm(("a", "b"), "alice")["edge"], ("a", "b"))

    def test_no_confidence(self):
        self.assertNotIn("confidence", infer([]))
