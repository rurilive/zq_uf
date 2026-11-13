import unittest

from glossary import (
    conflicts,
    define,
    fields_of,
    history,
    lookup,
    map_field,
    notifications,
    rename,
    reset,
    search,
    terms_of,
)


class GlossaryTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_exact_lookup(self):
        define({"name": "GMV", "definition": "成交总额"})
        self.assertEqual(lookup("GMV")["definition"], "成交总额")

    def test_alias_not_found(self):
        define({"name": "GMV", "definition": "成交总额", "aliases": ["成交额"]})
        self.assertIsNone(lookup("成交额"))

    def test_redefine_overwrites_history(self):
        define({"name": "GMV", "definition": "v1"})
        define({"name": "GMV", "definition": "v2"})
        self.assertEqual(lookup("GMV")["definition"], "v2")
        self.assertNotIn("history", lookup("GMV"))

    def test_search_by_alias(self):
        define({"name": "GMV", "definition": "成交总额",
                "aliases": ["成交额"]})
        results = search("成交额")
        self.assertEqual(results[0]["name"], "GMV")
        self.assertEqual(results[0]["matched"], "alias")
        self.assertIn("成交额", results[0]["snippet"])

    def test_search_by_abbreviation_and_description(self):
        define({"name": "成交总额", "abbreviations": ["GMV"],
                "definition": "统计周期内全部已支付订单金额合计，含退款"})
        by_abbr = search("gmv")
        self.assertEqual(by_abbr[0]["name"], "成交总额")
        self.assertEqual(by_abbr[0]["matched"], "abbreviation")
        by_desc = search("含退款")
        self.assertEqual(by_desc[0]["matched"], "definition")
        self.assertIn("含退款", by_desc[0]["snippet"])

    def test_term_maps_fields_across_tables(self):
        define({"name": "GMV", "definition": "成交总额"})
        map_field("orders", "pay_amount", "GMV", owner="张三")
        map_field("daily_report", "gmv_sum", "GMV", owner="李四")
        self.assertEqual(fields_of("GMV"),
                         ["daily_report.gmv_sum", "orders.pay_amount"])

    def test_field_holds_multiple_terms(self):
        define({"name": "GMV", "definition": "成交总额"})
        define({"name": "实收金额", "definition": "扣除退款后的金额"})
        map_field("orders", "pay_amount", "GMV")
        map_field("orders", "pay_amount", "实收金额")
        self.assertEqual(terms_of("orders", "pay_amount"),
                         ["GMV", "实收金额"])

    def test_rename_keeps_history_and_remaps_fields(self):
        define({"name": "GMV", "definition": "v1"})
        define({"name": "GMV", "definition": "v2"})
        map_field("orders", "pay_amount", "GMV")
        rename("GMV", "成交总额")
        self.assertIsNone(lookup("GMV"))
        self.assertEqual(lookup("成交总额")["definition"], "v2")
        past = history("成交总额")
        self.assertEqual([r["definition"] for r in past], ["v1", "v2"])
        self.assertEqual(past[-1]["name"], "GMV")
        self.assertEqual(fields_of("成交总额"), ["orders.pay_amount"])

    def test_conflict_keeps_both_sources(self):
        define({"name": "GMV", "definition": "含退款",
                "source": "交易域"})
        define({"name": "GMV", "definition": "不含退款",
                "source": "财务域"})
        self.assertEqual(lookup("GMV")["definition"], "不含退款")
        conflict = conflicts()[0]
        self.assertEqual(conflict["kept"]["source"], "财务域")
        self.assertEqual(conflict["previous"]["source"], "交易域")

    def test_change_notifies_field_owner(self):
        define({"name": "GMV", "definition": "v1"})
        map_field("orders", "pay_amount", "GMV", owner="张三")
        define({"name": "GMV", "definition": "v2"})
        notes = notifications(owner="张三")
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["field"], "orders.pay_amount")
        self.assertEqual(notes[0]["change"], "redefined")

    def test_history_keeps_old_definition(self):
        define({"name": "GMV", "definition": "含退款口径"})
        define({"name": "GMV", "definition": "不含退款口径"})
        self.assertEqual(history("GMV")[0]["definition"], "含退款口径")
