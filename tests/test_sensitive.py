import unittest

from dataclass import adjust, discover, export, hits, report, rescan, samples
from dataclass import sensitive

TABLE = {
    "name": "crm.users",
    "columns": [
        {"name": "phone", "comment": "手机号", "values": ["13800001111", "13900002222"]},
        {"name": "email_addr", "comment": "邮箱", "values": ["a@example.com"]},
        {"name": "nick", "comment": "昵称", "values": ["alice"]},
    ],
}

# 同一类数据、不同列名、不同表
ORDERS = {
    "name": "crm.orders",
    "columns": [
        {"name": "contact_mobile", "comment": "联系电话", "values": ["13700003333"]},
        {"name": "amount", "comment": "金额", "values": ["9.9"]},
    ],
}

IDCARD_TABLE = {
    "name": "hr.staff",
    "columns": [
        {"name": "c1", "comment": "", "values": ["11010119900307777X", "110101199003077778"]},
        {"name": "card_no", "comment": "银行卡", "values": ["6222020200112233445"]},
        {"name": "home", "comment": "", "values": ["北京市海淀区中关村大街1号"]},
    ],
}


def _reset():
    sensitive._RESULTS.clear()
    sensitive._TABLES.clear()
    sensitive._OVERRIDES.clear()
    sensitive._AFFECTED.clear()


class DiscoverTest(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_content_match_without_name_rule(self):
        # email_addr 不在旧词表里，靠内容命中
        result = discover(TABLE)
        self.assertEqual(result["sensitive"], 2)
        email = result["columns"][1]
        self.assertEqual(email["column"], "email_addr")
        self.assertEqual(email["kind"], "contact")
        self.assertIn("content:email", email["evidence"])

    def test_hit_gives_position_and_samples(self):
        phone = discover(TABLE)["columns"][0]
        self.assertEqual(phone["index"], 0)
        self.assertEqual(phone["matched_rows"], [0, 1])
        self.assertEqual(samples("crm.users", "phone"),
                         [{"row": 0, "value": "13800001111"},
                          {"row": 1, "value": "13900002222"}])

    def test_same_kind_same_level_across_tables(self):
        discover(TABLE)
        discover(ORDERS)
        phone_level = hits("crm.users")[0]["level"]
        mobile = hits("crm.orders")[0]
        self.assertEqual(mobile["kind"], "contact")
        self.assertEqual(mobile["level"], phone_level)

    def test_pattern_categories(self):
        found = {c["column"]: c for c in discover(IDCARD_TABLE)["columns"]}
        self.assertEqual(found["c1"]["kind"], "id")
        self.assertEqual(found["c1"]["level"], "高")
        self.assertEqual(found["card_no"]["kind"], "account")
        self.assertEqual(found["home"]["kind"], "address")
        # 身份证 18 位不能被银行卡模式吃掉
        self.assertEqual(found["c1"]["matched_rows"], [0, 1])

    def test_name_only_hit_still_reported(self):
        table = {"name": "crm.empty", "columns": [
            {"name": "idcard", "comment": "身份证", "values": []},
        ]}
        found = discover(table)["columns"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["kind"], "id")
        self.assertIn("name:idcard", found[0]["evidence"])

    def test_non_sensitive_column_passes(self):
        discover(TABLE)
        self.assertEqual(samples("crm.users", "nick"), [])


class AdjustTest(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_adjust_remembered_and_rescan_affected_only(self):
        discover(TABLE)
        discover(ORDERS)
        adjust("crm.users", "email_addr", level="高")
        self.assertEqual(sensitive._AFFECTED, {"crm.users"})
        rescanned = rescan()
        self.assertEqual(rescanned, ["crm.users"])
        email = [c for c in hits("crm.users") if c["column"] == "email_addr"][0]
        self.assertEqual(email["level"], "高")
        self.assertTrue(email["adjusted"])
        # 重扫只动了受影响的表
        self.assertEqual(sensitive._AFFECTED, set())

    def test_adjust_can_mark_not_sensitive(self):
        discover(TABLE)
        adjust("crm.users", "email_addr", sensitive=False)
        rescan()
        self.assertEqual([c["column"] for c in hits("crm.users")], ["phone"])

    def test_adjust_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            adjust("crm.users", "phone", kind="unknown")


class ReportTest(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_report_counts_by_database(self):
        discover(TABLE)
        discover(ORDERS)
        discover(IDCARD_TABLE)
        result = report()
        self.assertEqual(result["sensitive"], 6)
        self.assertEqual(result["tables"], 3)
        self.assertEqual(result["databases"]["crm"]["sensitive"], 3)
        self.assertEqual(result["databases"]["crm"]["tables"], 2)
        self.assertEqual(result["databases"]["hr"]["levels"], {"高": 2, "低": 1})

    def test_export_per_database(self):
        discover(TABLE)
        discover(IDCARD_TABLE)
        rows = export("crm")
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["database"] for r in rows}, {"crm"})
        self.assertEqual(rows[0]["kind_label"], "联系方式")
        self.assertEqual(len(export()), 5)  # crm 2 + hr 3


if __name__ == "__main__":
    unittest.main()
