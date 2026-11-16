import unittest

from symrecover import lookup, recover, report
from symrecover import symbols

BINARY = {
    "name": "routerd",
    "version": "1.0",
    "functions": [
        {"address": "0804a100", "calls": ["0804a200"], "strings": ["admin"]},
        {"address": "0804a200", "calls": [], "strings": ["/tmp/key"]},
    ],
}


class SymbolsTest(unittest.TestCase):
    def setUp(self):
        symbols._registry.clear()

    def test_name_comes_from_address(self):
        self.assertEqual(recover(BINARY)["names"][0]["name"], "sub_0804a100")

    def test_evidence_is_empty(self):
        result = recover(BINARY)
        self.assertEqual(result["names"][1]["evidence"], "")

    def test_unmatched_is_empty(self):
        self.assertEqual(recover(BINARY)["unmatched"], [])

    def test_lookup_returns_entry(self):
        recover(BINARY)
        self.assertEqual(lookup("routerd", "0804a200")["name"], "sub_0804a200")

    def test_report_counts_recovered(self):
        recover(BINARY)
        self.assertEqual(report()["recovered"], 2)
