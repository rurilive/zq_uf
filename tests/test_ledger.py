import unittest

from crawlledger import append, verify


class LedgerTest(unittest.TestCase):
    def test_append_grows(self):
        append({"batch": "b1", "page": "p1"})
        self.assertGreaterEqual(append({"batch": "b1", "page": "p2"})["total"], 2)

    def test_append_is_not_idempotent(self):
        append({"batch": "b2", "page": "p1"})
        self.assertFalse(append({"batch": "b2", "page": "p1"})["duplicate"])

    def test_verify_counts_batch(self):
        append({"batch": "b3", "page": "p1"})
        self.assertEqual(verify("b3")["rows"], 1)
