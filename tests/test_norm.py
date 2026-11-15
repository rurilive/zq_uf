import json
import os
import tempfile
import unittest

from urlnorm2 import add, same
from urlnorm2.index import URLNormIndex
from urlnorm2.rules import Rule, builtin_rules, make_strip_query_rule


class NormTest(unittest.TestCase):
    def test_same_ignores_case_and_slash(self):
        self.assertTrue(same("https://A/p/", "https://a/p"))

    def test_same_rejects_different_paths(self):
        self.assertFalse(same("https://a/p", "https://a/q"))

    def test_add_reports_class_count(self):
        add("https://a/1")
        self.assertGreaterEqual(add("https://a/2")["classes"], 2)


class IncrementalMergeTest(unittest.TestCase):
    def setUp(self):
        self.idx = URLNormIndex()

    def test_equivalent_url_merges_into_existing_class(self):
        first = self.idx.add("https://shop/p/1")
        second = self.idx.add("https://Shop/p/1/")
        self.assertEqual(first["action"], "created")
        self.assertEqual(second["action"], "merged")
        self.assertEqual(second["class_id"], first["class_id"])
        self.assertEqual(second["merged_from"], [first["class_id"]])
        self.assertEqual(second["class_size"], 2)
        # 类数量不增加，证明是增量并入而非重建
        self.assertEqual(self.idx.size()["classes"], 1)

    def test_distinct_urls_create_distinct_classes(self):
        self.idx.add("https://a/1")
        report = self.idx.add("https://a/2")
        self.assertEqual(report["action"], "created")
        self.assertEqual(self.idx.size()["classes"], 2)

    def test_re_add_is_idempotent_and_loses_nothing(self):
        self.idx.add("https://a/p")
        self.idx.add("https://A/P/")
        again = self.idx.add("https://a/p")
        self.assertEqual(again["action"], "existing")
        self.assertEqual(self.idx.size()["urls"], 2)
        self.assertEqual(self.idx.size()["classes"], 1)

    def test_fragment_and_query_report_fired_rules(self):
        report = self.idx.add("https://a/p#section")
        self.assertIn("strip_fragment", report["fired_rules"])


class RepresentativeStabilityTest(unittest.TestCase):
    def setUp(self):
        self.idx = URLNormIndex()

    def test_representative_is_earliest_url_and_survives_merges(self):
        first = self.idx.add("https://a/p")
        self.idx.add("https://A/P/")
        self.idx.add("https://a/p#f")
        rep = self.idx.representative_of("https://A/P/")
        self.assertEqual(rep, "https://a/p")
        self.assertEqual(first["representative"], "https://a/p")

    def test_representative_moves_to_next_earliest_when_expired(self):
        self.idx.add("https://a/p")
        self.idx.add("https://A/P/")
        result = self.idx.remove("https://a/p")
        self.assertTrue(result["representative_changed"])
        self.assertEqual(result["representative"], "https://A/P/")
        self.assertEqual(self.idx.representative_of("https://A/P/"), "https://A/P/")

    def test_class_id_stable_across_unrelated_adds(self):
        cid = self.idx.add("https://a/1")["class_id"]
        for i in range(10):
            self.idx.add("https://b/%d" % i)
        self.assertEqual(self.idx.class_of("https://a/1"), cid)


class LocalRecomputeTest(unittest.TestCase):
    def setUp(self):
        self.idx = URLNormIndex()
        self.idx.add("https://a/x?utm_source=z&keep=1")
        self.idx.add("https://a/x?keep=1")
        self.plain_cid = self.idx.add("https://b/plain")["class_id"]
        self.plain_rep = "https://b/plain"

    def test_new_rule_merges_only_affected_classes(self):
        before = self.idx.size()["classes"]
        self.idx._normalizer.add_rule(make_strip_query_rule(["utm_source"]))
        report = self.idx.recompute()
        self.assertEqual(len(report["recomputed_class_ids"]), 1)
        self.assertNotIn(self.plain_cid, report["recomputed_class_ids"])
        self.assertEqual(self.idx.size()["classes"], before - 1)
        self.assertTrue(self.idx.same(
            "https://a/x?utm_source=z&keep=1", "https://a/x?keep=1"))
        merge = report["merges"][0]
        self.assertIn(report["recomputed_class_ids"][0], merge["from"])
        # 未受影响类 id 与代表原样保留
        self.assertEqual(self.idx.class_info(self.plain_cid)["representative"],
                         self.plain_rep)

    def test_rule_change_splits_a_class(self):
        self.idx.add("https://c/p")
        self.idx.add("https://c/p#frag")
        # setUp 的三个类 + 新加入的两个等价 URL（同属一类）= 4 类
        self.assertEqual(self.idx.size()["classes"], 4)
        report = self.idx.update_rule("strip_fragment", lambda value: value)
        self.assertEqual(len(report["splits"]), 1)
        split = report["splits"][0]
        self.assertEqual(len(split["to"]), 2)
        self.assertEqual(self.idx.size()["classes"], 5)
        # 拆分后两 URL 分属不同类；same 按当前规则回答"不再等价"
        self.assertNotEqual(self.idx.class_of("https://c/p"),
                            self.idx.class_of("https://c/p#frag"))
        self.assertFalse(self.idx.same("https://c/p", "https://c/p#frag"))
        # 原代表所在簇复用旧类 id 且代表不变
        self.assertEqual(self.idx.representative_of("https://c/p"), "https://c/p")

    def test_unaffected_classes_untouched_when_rule_changes(self):
        # 只影响带查询串的类，普通类完全不进入重建
        self.idx._normalizer.add_rule(make_strip_query_rule(["utm_source"]))
        report = self.idx.recompute()
        self.assertEqual(report["recomputed_class_ids"], [1])
        info = self.idx.class_info(self.plain_cid)
        self.assertEqual(info["representative"], self.plain_rep)
        self.assertEqual(info["size"], 1)

    def test_benign_rule_change_rebuilds_nothing(self):
        report = self.idx.update_rule(
            "strip_fragment", lambda value: value.split("#", 1)[0])
        self.assertEqual(report["recomputed"], 0)
        self.assertEqual(report["splits"], [])
        self.assertEqual(self.idx.size()["urls"], 3)

    def test_queries_serve_live_index_during_rebuild(self):
        seen = []
        utm_a = "https://a/x?utm_source=z&keep=1"
        utm_b = "https://a/x?keep=1"

        def progress(stage, detail):
            seen.append(stage)
            if stage == "rebuilt":
                # 切换前：活索引仍是合并前的分组，类数量/归属保持可读且一致
                self.assertEqual(self.idx.size()["classes"], 3)
                self.assertNotEqual(self.idx.class_of(utm_a),
                                    self.idx.class_of(utm_b))
                self.assertEqual(self.idx.class_info(self.plain_cid)["size"], 1)
            if stage == "swap":
                self.assertEqual(self.idx.size()["classes"], 2)
                self.assertEqual(self.idx.class_of(utm_a),
                                 self.idx.class_of(utm_b))

        self.idx._normalizer.add_rule(make_strip_query_rule(["utm_source"]))
        self.idx.recompute(progress=progress)
        self.assertEqual(seen, ["start", "detected", "rebuilt", "swap"])


class SameRuleTest(unittest.TestCase):
    def setUp(self):
        self.idx = URLNormIndex()

    def test_result_is_bool_and_carries_rule(self):
        result = self.idx.same("https://A/p/", "https://a/p")
        self.assertTrue(result)
        self.assertEqual(result.rule, "lowercase")
        self.assertEqual(result.rule_version, 1)

    def test_raw_equality_has_no_rule(self):
        result = self.idx.same("https://a/p", "https://a/p")
        self.assertTrue(result)
        self.assertIsNone(result.rule)

    def test_inequivalent_result(self):
        result = self.idx.same("https://a/p", "https://a/q")
        self.assertFalse(result)
        self.assertIsNone(result.rule)

    def test_same_works_for_urls_never_added(self):
        self.assertTrue(self.idx.same("https://z/x#1", "https://Z/X"))


class ExpireTest(unittest.TestCase):
    def setUp(self):
        self.idx = URLNormIndex()
        self.idx.add("https://a/p")
        self.idx.add("https://A/P/")
        self.idx.add("https://a/q")

    def test_remove_keeps_class_and_relations(self):
        result = self.idx.remove("https://A/P/")
        self.assertEqual(result["action"], "removed")
        self.assertEqual(result["class_size"], 1)
        self.assertEqual(self.idx.size()["urls"], 2)

    def test_remove_last_member_drops_class(self):
        self.idx.remove("https://A/P/")
        result = self.idx.remove("https://a/p")
        self.assertEqual(result["action"], "class_dropped")
        self.assertIsNone(self.idx.class_of("https://a/p"))
        self.assertEqual(self.idx.size()["classes"], 1)

    def test_remove_unknown_is_noop(self):
        self.assertEqual(self.idx.remove("https://never/seen")["action"], "not_found")

    def test_remove_many_batch(self):
        summary = self.idx.remove_many(["https://A/P/", "https://a/q"])
        self.assertEqual(summary["removed"], 2)
        self.assertEqual(summary["classes_dropped"], 1)
        self.assertEqual(self.idx.size()["urls"], 1)

    def test_removed_url_can_rejoin_as_new_member(self):
        self.idx.remove("https://A/P/")
        report = self.idx.add("https://A/P/")
        self.assertEqual(report["action"], "merged")
        self.assertEqual(self.idx.size()["urls"], 3)


class SizeViewTest(unittest.TestCase):
    def test_size_and_class_info(self):
        idx = URLNormIndex()
        idx.add("https://a/1")
        idx.add("https://A/1/")
        idx.add("https://a/2")
        view = idx.size()
        self.assertEqual(view["urls"], 3)
        self.assertEqual(view["classes"], 2)
        self.assertEqual(sum(view["class_sizes"].values()), 3)
        cid = idx.class_of("https://a/1")
        info = idx.class_info(cid)
        self.assertEqual(info["size"], 2)
        self.assertEqual(info["members"][0], "https://a/1")
        self.assertIn("rule_versions", idx.stats())


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "index.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_and_load_round_trip(self):
        idx = URLNormIndex()
        idx.add("https://a/p")
        idx.add("https://A/P/")
        idx.add("https://a/q")
        saved = idx.save(self.path)
        self.assertTrue(os.path.exists(self.path))
        self.assertEqual(saved["urls"], 3)

        restored = URLNormIndex.load(self.path)
        self.assertEqual(restored.size()["urls"], 3)
        self.assertEqual(restored.size()["classes"], 2)
        self.assertEqual(restored.representative_of("https://A/P/"), "https://a/p")
        self.assertEqual(restored.class_of("https://a/q"), idx.class_of("https://a/q"))
        # 序号继续递增，后续代表口径不回退
        report = restored.add("https://new/z")
        self.assertEqual(report["action"], "created")
        self.assertEqual(restored.recovery["recomputed"], None)

    def test_load_with_rule_drift_recomputes_only_affected(self):
        idx = URLNormIndex()
        idx._normalizer.add_rule(make_strip_query_rule(["utm_source"]))
        idx.add("https://a/x?utm_source=z&keep=1")
        idx.add("https://a/x?keep=1&utm_source=q")
        idx.add("https://b/plain")
        idx.recompute()
        idx.save(self.path)

        upgraded = builtin_rules() + [
            make_strip_query_rule(["utm_source", "keep"], version=2)]
        restored = URLNormIndex.load(self.path, rules=upgraded)
        self.assertIn("strip_tracking_query", restored.recovery["rule_drift"])
        self.assertEqual(len(restored.recovery["recomputed"]), 1)
        self.assertTrue(restored.same("https://a/x?utm_source=z&keep=1",
                                      "https://a/x?keep=1&utm_source=q"))
        # 未受影响的普通类仍然可查、代表不变
        self.assertEqual(restored.representative_of("https://b/plain"),
                         "https://b/plain")

    def test_load_rejects_unknown_rule_snapshot(self):
        idx = URLNormIndex()
        idx.add("https://a/p")
        idx.save(self.path)
        with open(self.path, encoding="utf-8") as fh:
            payload = json.load(fh)
        payload["rule_versions"]["ghost_rule"] = 1
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        with self.assertRaises(ValueError):
            URLNormIndex.load(self.path)

    def test_snapshot_is_json(self):
        idx = URLNormIndex()
        idx.add("https://a/p")
        idx.save(self.path)
        with open(self.path, encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertEqual(payload["format"], 1)
        self.assertEqual(payload["entries"][0]["url"], "https://a/p")


if __name__ == "__main__":
    unittest.main()
