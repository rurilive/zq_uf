import unittest

from vulnretest import history, retest

vuln = {
    "id": "V-7",
    "project": "shop",
    "steps": [
        {"name": "提交恶意参数", "ok": True},
        {"name": "查看响应回显", "ok": True},
    ],
}


class RetestTest(unittest.TestCase):
    def test_pass_closes_ticket(self):
        result = retest(dict(vuln))
        self.assertEqual(result["result"], "pass")
        self.assertTrue(result["closed"])

    def test_fail_keeps_open_without_reason(self):
        broken = {"id": "V-8", "project": "shop", "steps": [{"name": "回显", "ok": False}]}
        result = retest(broken)
        self.assertEqual(result["result"], "fail")
        self.assertFalse(result["closed"])
        self.assertEqual(result["failures"], [])

    def test_no_diff_between_rounds(self):
        result = retest(dict(vuln))
        self.assertEqual(result["diff"], [])
        self.assertIsNone(result["baseline"])

    def test_history_has_no_time(self):
        retest(dict(vuln))
        records = history("V-7")["records"]
        self.assertEqual(records[0]["at"], None)

    def test_history_counts_rounds(self):
        retest({"id": "V-9", "project": "blog", "steps": [{"name": "a", "ok": True}]})
        retest({"id": "V-9", "project": "blog", "steps": [{"name": "a", "ok": True}]})
        self.assertEqual(history("V-9")["count"], 2)
        self.assertEqual(history("V-9")["projects"], [])


if __name__ == "__main__":
    unittest.main()
