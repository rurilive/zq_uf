import unittest

from datasearch import SearchIndex, search, search_detail


class SearchTest(unittest.TestCase):
    def test_matches_name(self):
        self.assertEqual([t["name"] for t in search("orders")], ["dwd_orders"])

    def test_description_not_searched(self):
        self.assertEqual(search("订单明细"), [])

    def test_case_sensitive(self):
        self.assertEqual(search("DWD"), [])

    def test_no_score(self):
        self.assertEqual(set(search("orders")[0].keys()), {"name", "desc", "fields"})


class FieldSearchTest(unittest.TestCase):
    def test_matches_field_name(self):
        self.assertEqual([t["name"] for t in search("amount")], ["dwd_orders"])
        self.assertEqual([t["name"] for t in search("city")], ["dim_user"])

    def test_matches_description_token(self):
        self.assertEqual([t["name"] for t in search("明细")], ["dwd_orders"])

    def test_full_phrase_not_tokenized(self):
        # 查询词不再切词，整词无命中即为空
        self.assertEqual(search("订单明细"), [])


class SynonymTest(unittest.TestCase):
    def test_chinese_query_finds_english_table(self):
        self.assertEqual([t["name"] for t in search("订单")], ["dwd_orders", "dws_sales"])

    def test_english_query_finds_chinese_desc(self):
        names = [t["name"] for t in search("user")]
        self.assertEqual(names[0], "dim_user")

    def test_abbreviation(self):
        index = SearchIndex()
        index.add_table("dws_sales_daily", "销售成交额汇总", ["order_id", "gmv"])
        self.assertEqual([t["name"] for t in index.search("成交额")], ["dws_sales_daily"])
        self.assertEqual([t["name"] for t in index.search("gmv")], ["dws_sales_daily"])


class PrefixTest(unittest.TestCase):
    def test_prefix_match(self):
        self.assertEqual([t["name"] for t in search("amo")], ["dwd_orders"])

    def test_min_prefix_len_boundary(self):
        self.assertEqual([t["name"] for t in search("am")], ["dwd_orders"])
        self.assertEqual(search("a"), [])

    def test_prefix_not_substring(self):
        self.assertEqual(search("rder"), [])


class RankingTest(unittest.TestCase):
    def setUp(self):
        self.index = SearchIndex()
        self.index.add_table("alpha_logs", "日志", ["msg"], popularity=10)
        self.index.add_table("beta_logs", "日志", ["msg"], popularity=99)
        self.index.add_table("payment", "支付流水", ["amount"], popularity=0)
        self.index.add_table("amount_flow", "流水", ["rid"], popularity=0)

    def test_popularity_breaks_ties(self):
        self.assertEqual(
            [t["name"] for t in self.index.search("logs")],
            ["beta_logs", "alpha_logs"],
        )

    def test_name_beats_field(self):
        self.assertEqual(
            [t["name"] for t in self.index.search("amount")],
            ["amount_flow", "payment"],
        )

    def test_multi_term_requires_all(self):
        self.assertEqual([t["name"] for t in search("dwd orders")], ["dwd_orders"])


class SnippetTest(unittest.TestCase):
    def test_detail_has_score_and_snippets(self):
        hits = search_detail("订单")
        self.assertEqual(hits[0]["table"]["name"], "dwd_orders")
        self.assertGreater(hits[0]["score"], 0)
        wheres = {snippet["where"] for snippet in hits[0]["snippets"]}
        self.assertIn("name", wheres)
        self.assertIn("desc", wheres)

    def test_search_result_keys_stay_stable(self):
        index = SearchIndex()
        index.add_table("t1", "d", ["f"], tags=["x"], popularity=5)
        self.assertEqual(set(index.search("t1")[0].keys()), {"name", "desc", "fields"})

    def test_results_are_copies(self):
        search("orders")[0]["fields"].append("hacked")
        self.assertEqual(search("orders")[0]["fields"], ["order_id", "amount"])


class SerializationTest(unittest.TestCase):
    def test_round_trip_reproduces_results(self):
        import json

        index = SearchIndex()
        index.add_table("dwd_order_detail", "订单交易明细", ["order_id", "amount"],
                        tags=["trade"], popularity=320)
        index.add_table("dws_sales_daily", "销售成交额汇总", ["order_id", "gmv"],
                        tags=["sales"], popularity=260)
        payload = json.dumps(index.to_dict(), ensure_ascii=False)
        restored = SearchIndex.from_dict(json.loads(payload))
        self.assertEqual(restored.search_detail("订单"), index.search_detail("订单"))
        self.assertEqual(restored.search("amount"), index.search("amount"))


class ErrorTest(unittest.TestCase):
    def test_empty_query(self):
        with self.assertRaises(ValueError):
            search("")
        with self.assertRaises(ValueError):
            search("   ")

    def test_non_string_query(self):
        with self.assertRaises(TypeError):
            search(None)
        with self.assertRaises(TypeError):
            search(123)

    def test_add_table_validation(self):
        index = SearchIndex()
        with self.assertRaises(ValueError):
            index.add_table("", "d", ["f"])
        with self.assertRaises(ValueError):
            index.add_table("t", "d", [])
        with self.assertRaises(TypeError):
            index.add_table("t", 1, ["f"])
        with self.assertRaises(ValueError):
            index.add_table("t", "d", ["f"], popularity=-1)
        with self.assertRaises(TypeError):
            index.add_table("t", "d", ["f"], popularity="hot")
        index.add_table("t", "d", ["f"])
        with self.assertRaises(ValueError):
            index.add_table("t", "d", ["f"])

    def test_from_dict_validation(self):
        with self.assertRaises(TypeError):
            SearchIndex.from_dict([])
        with self.assertRaises(ValueError):
            SearchIndex.from_dict({"version": 99, "tables": []})
        with self.assertRaises(ValueError):
            SearchIndex.from_dict({"version": 1})
        with self.assertRaises(ValueError):
            SearchIndex.from_dict({"version": 1, "tables": [{"name": "t"}]})
