import unittest

from auditlog import append, tail, verify
from auditlog import chain

DAY1 = {"day": "2026-01-01", "action": "login", "user": "alice"}
DAY2 = {"day": "2026-01-02", "action": "read", "user": "bob"}


class ChainTest(unittest.TestCase):
    def setUp(self):
        chain._records.clear()

    def test_append_starts_at_zero(self):
        self.assertEqual(append(DAY1)["seq"], 0)

    def test_append_has_no_link(self):
        append(DAY1)
        append(DAY2)
        self.assertEqual(append(DAY1)["link"], "")

    def test_verify_ignores_span(self):
        append(DAY1)
        append(DAY2)
        result = verify((1, 1))
        self.assertEqual(result["checked"], 2)
        self.assertEqual(result["start"], 0)

    def test_verify_detects_tamper(self):
        append(DAY1)
        chain._records[0]["record"]["action"] = "deleted"
        result = verify()
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken"], 0)

    def test_tail_returns_last_digest(self):
        append(DAY1)
        digest = append(DAY2)["digest"]
        self.assertEqual(tail()["digest"], digest)
