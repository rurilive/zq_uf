import unittest

from dataclass import discover, hits, report
from dataclass import sensitive

TABLE = {
    "name": "crm.users",
    "columns": [
        {"name": "phone", "comment": "手机号", "values": ["13800001111", "13900002222"]},
        {"name": "email_addr", "comment": "邮箱", "values": ["a@example.com"]},
        {"name": "nick", "comment": "昵称", "values": ["alice"]},
    ],
}


class SensitiveTest(unittest.TestCase):
    def setUp(self):
        sensitive._RESULTS.clear()

    def test_only_exact_name_match(self):
        result = discover(TABLE)
        self.assertEqual(result["sensitive"], 1)
        self.assertEqual(result["columns"][0]["column"], "phone")

    def test_alias_column_is_missed(self):
        discover(TABLE)
        self.assertEqual(hits("crm.users"), [{"column": "phone", "index": 0, "kind": "phone", "level": "", "samples": [], "evidence": "name"}])

    def test_level_is_empty(self):
        self.assertEqual(discover(TABLE)["columns"][0]["level"], "")

    def test_no_content_samples(self):
        self.assertEqual(discover(TABLE)["columns"][0]["samples"], [])

    def test_report_counts_tables(self):
        discover(TABLE)
        self.assertEqual(report()["tables"], 1)
        self.assertEqual(report()["sensitive"], 1)
