import json
import os
import tempfile
import unittest

from rowfold import RowFold

# 两家站点对同一商户的主键不同，字段多寡不同。
CLEAN = [
    # 字段少的记录先到
    {"key": "s1-100", "row_id": "s1-100", "source": "site1", "name": "老王牛肉面",
     "amount": 120, "city": "上海", "updated_at": "2026-01-02"},
    # 字段全的后到
    {"key": "shop-77", "row_id": "shop-77", "source": "site2", "name": "老王牛肉面",
     "phone": "021-8888", "city": "上海", "contact": "王强", "amount": 120,
     "updated_at": "2026-03-05"},
]


def session(**options):
    return RowFold(**options)


class SameKeyTest(unittest.TestCase):
    def test_more_complete_row_wins_even_if_later(self):
        fold = session()
        result = fold.fold([
            {"key": "a", "value": 1},
            {"key": "a", "value": 2, "extra": "x"},
        ])
        self.assertEqual(result["masters"][0]["row_id"], "a#1")
        self.assertEqual(result["masters"][0]["row"]["extra"], "x")

    def test_tie_breaks_on_updated_at(self):
        fold = session()
        result = fold.fold([
            {"key": "a", "value": 1, "updated_at": "2026-01-01"},
            {"key": "a", "value": 2, "updated_at": "2026-02-01"},
        ])
        self.assertEqual(result["masters"][0]["row"]["value"], 2)

    def test_tie_breaks_on_arrival_order(self):
        fold = session()
        result = fold.fold([
            {"key": "a", "value": 1},
            {"key": "a", "value": 2},
        ])
        self.assertEqual(result["masters"][0]["row"]["value"], 1)

    def test_earliest_but_incomplete_row_keeps_reason(self):
        fold = session()
        result = fold.fold(CLEAN, domain="merchant")
        decision = result["reasons"][0]["decision"]
        self.assertIn("s1-100", decision)
        self.assertIn("shop-77", decision)

    def test_folded_count(self):
        fold = session()
        self.assertEqual(
            fold.fold([{"key": "b"}, {"key": "b"}])["folded"], 1
        )


class SimilarityTest(unittest.TestCase):
    def test_different_keys_high_overlap_merge(self):
        fold = session()
        result = fold.fold(CLEAN, domain="merchant")
        self.assertEqual(len(result["rows"]), 1)
        master = result["masters"][0]
        self.assertEqual(master["row_id"], "shop-77")
        # 合并后的主记录用完整行补齐
        self.assertEqual(master["row"]["phone"], "021-8888")
        self.assertEqual(master["row"]["contact"], "王强")
        self.assertEqual(master["merged_count"], 1)

    def test_low_similarity_not_merged(self):
        fold = session()
        result = fold.fold([
            {"key": "x", "name": "甲店", "city": "上海", "phone": "1", "amount": 10},
            {"key": "y", "name": "乙店", "city": "北京", "phone": "2", "amount": 10},
        ])
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["folded"], 0)

    def test_threshold_is_configurable(self):
        rows = [
            {"key": "x", "name": "甲店", "city": "上海", "phone": "1", "amount": 10},
            {"key": "y", "name": "甲店", "city": "北京", "phone": "1", "amount": 10},
        ]
        # 3 个关键字段中 2 个一致 → 0.67
        self.assertEqual(len(session(threshold=0.6).fold(rows)["rows"]), 1)
        self.assertEqual(len(session(threshold=0.8).fold(rows)["rows"]), 2)

    def test_min_shared_blocks_single_field_match(self):
        fold = session(min_shared=3)
        rows = [
            {"key": "x", "name": "甲店", "city": "上海", "phone": "1", "amount": 10},
            {"key": "y", "name": "甲店", "city": "北京", "phone": "2", "amount": 10},
        ]
        result = fold.fold(rows)
        self.assertEqual(len(result["rows"]), 2)
        self.assertTrue(result["config"]["cross_key_merge_enabled"])

    def test_too_few_fields_with_explicit_min_shared_raises(self):
        fold = session(similarity_fields=("name",), min_shared=2)
        with self.assertRaises(ValueError):
            fold.fold([{"key": "x", "name": "甲"}, {"key": "y", "name": "甲"}])

    def test_derived_defaults_disable_cross_key_merge_visibly(self):
        fold = session()
        result = fold.fold([{"key": "x", "name": "甲"}, {"key": "y", "name": "甲"}])
        self.assertFalse(result["config"]["cross_key_merge_enabled"])
        self.assertIn("只按主键去重", result["config"]["cross_key_merge_disabled_reason"])
        self.assertEqual(len(result["rows"]), 2)

    def test_similarity_reason_records_score_and_fields(self):
        fold = session()
        result = fold.fold(CLEAN)
        reason = result["reasons"][0]
        self.assertEqual(reason["basis"], "similarity")
        self.assertGreaterEqual(reason["score"], 0.8)
        self.assertIn("name", reason["shared_fields"])
        self.assertEqual(reason["source"], "site1")


class ExplainTest(unittest.TestCase):
    def test_merged_record_queryable_by_its_own_key(self):
        fold = session()
        fold.fold(CLEAN)
        view = fold.explain("s1-100")
        self.assertEqual(view["status"], "merged")
        self.assertEqual(view["master"]["row_id"], "shop-77")
        self.assertEqual(view["focus"]["row_id"], "s1-100")
        self.assertEqual(view["focus"]["master"], "shop-77")

    def test_merged_record_queryable_by_row_id(self):
        fold = session()
        fold.fold(CLEAN)
        self.assertEqual(fold.explain("s1-100")["group"], fold.explain("shop-77")["group"])

    def test_sources_preserved(self):
        fold = session()
        fold.fold(CLEAN)
        view = fold.explain("shop-77")
        self.assertIn("site1", view["sources"])
        self.assertIn("site2", view["sources"])

    def test_unknown_key(self):
        with self.assertRaises(KeyError):
            session().explain("nope")


class AuditTest(unittest.TestCase):
    def test_initial_master_change_is_audited(self):
        fold = session()
        result = fold.fold(CLEAN, domain="merchant")
        changes = [e for e in result["audit"] if e["action"] == "master_change"]
        self.assertTrue(changes)
        self.assertEqual(changes[0]["previous"], "s1-100")
        self.assertEqual(changes[0]["current"], "shop-77")
        self.assertEqual(changes[0]["domain"], "merchant")

    def test_refold_master_change_is_audited(self):
        fold = session()
        fold.fold([{"key": "a", "row_id": "a#0", "value": 1}], domain="merchant")
        fold.fold(
            [
                {"key": "a", "row_id": "a#0", "value": 1},
                {"key": "a", "row_id": "a#9", "value": 2, "extra": "x"},
            ],
            domain="merchant",
        )
        refold = [e for e in fold.audit("a") if e.get("phase") == "refold"]
        self.assertTrue(refold)
        self.assertEqual(refold[0]["previous"], "a#0")
        self.assertEqual(refold[0]["current"], "a#9")

    def test_audit_scoped_to_record(self):
        fold = session()
        fold.fold(CLEAN, domain="merchant")
        fold.fold([{"key": "other", "value": 1}], domain="order")
        keys = {entry.get("key") for entry in fold.audit("s1-100")}
        self.assertNotIn("other", keys)


class ReplayTest(unittest.TestCase):
    def test_replay_lists_ordered_steps(self):
        fold = session(similarity_fields=("name", "city"))
        fold.fold([
            {"key": "x", "name": "甲店", "city": "上海", "contact": "王", "amount": 5},
            {"key": "y", "name": "甲店", "city": "上海", "phone": "9", "amount": 5},
        ])
        steps = fold.replay("y")["steps"]
        actions = [step["action"] for step in steps]
        self.assertEqual(actions[0], "select_master")
        self.assertIn("merge", actions)
        self.assertIn("fill_field", actions)
        self.assertEqual([step["step"] for step in steps], list(range(1, len(steps) + 1)))
        filled = [step for step in steps if step["action"] == "fill_field"]
        self.assertEqual(filled[0]["field"], "phone")

    def test_replay_from_merged_record_returns_same_group(self):
        fold = session()
        fold.fold(CLEAN)
        self.assertEqual(fold.replay("s1-100")["steps"], fold.replay("shop-77")["steps"])


class DryRunTest(unittest.TestCase):
    def test_dry_run_does_not_commit(self):
        fold = session()
        result = fold.fold(CLEAN, domain="merchant", dry_run=True)
        self.assertTrue(result["dry_run"])
        with self.assertRaises(KeyError):
            fold.explain("shop-77")
        self.assertEqual(fold.audit(), [])

    def test_promote_after_dry_run(self):
        fold = session()
        fold.dry_run(CLEAN, domain="merchant")
        promoted = fold.promote("merchant")
        self.assertFalse(promoted["dry_run"])
        self.assertTrue(promoted["promoted"])
        self.assertEqual(fold.explain("shop-77")["master"]["row_id"], "shop-77")

    def test_promote_without_dry_run(self):
        with self.assertRaises(RuntimeError):
            session().promote()

    def test_promote_domain_must_match(self):
        fold = session()
        fold.dry_run(CLEAN, domain="merchant")
        with self.assertRaises(ValueError):
            fold.promote("order")


class AmountCheckTest(unittest.TestCase):
    def test_amount_not_double_counted(self):
        fold = session()
        result = fold.fold(CLEAN)
        amounts = result["amounts"]
        self.assertEqual(amounts["before"], 240)
        self.assertEqual(amounts["after"], 120)
        self.assertEqual(amounts["duplicates_removed"], 120)
        self.assertTrue(result["checks"]["amount_not_increased"])
        self.assertTrue(result["safe_to_promote"])

    def test_amount_strings_normalized(self):
        fold = session()
        result = fold.fold([
            {"key": "a", "name": "甲", "city": "上海", "amount": "1,200.50"},
            {"key": "a", "name": "甲", "city": "上海", "amount": "1200.5"},
        ])
        self.assertEqual(result["amounts"]["after"], 1200.5)


class SplitTest(unittest.TestCase):
    def test_split_restores_independent_master(self):
        fold = session()
        fold.fold(CLEAN)
        out = fold.split("shop-77", ["s1-100"])
        self.assertEqual(out["detached"][0]["master_key"], "s1-100")
        self.assertEqual(fold.explain("s1-100")["master"]["row_id"], "s1-100")
        self.assertEqual(fold.explain("shop-77")["merged"], [])

    def test_split_writes_audit(self):
        fold = session()
        fold.fold(CLEAN)
        fold.split("shop-77", ["s1-100"])
        splits = [e for e in fold.audit() if e["action"] == "split"]
        self.assertEqual(len(splits), 1)
        self.assertEqual(splits[0]["row"], "s1-100")
        self.assertEqual(splits[0]["group"], "g1")

    def test_split_record_stays_separate_on_refold(self):
        fold = session()
        fold.fold(CLEAN)
        fold.split("shop-77", ["s1-100"])
        result = fold.fold(CLEAN)
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(result["amounts"]["after"], 240)
        self.assertEqual(result["kept_separate"][0]["row_id"], "s1-100")

    def test_unsplit_allows_merge_again(self):
        fold = session()
        fold.fold(CLEAN)
        fold.split("shop-77", ["s1-100"])
        fold.unsplit("s1-100")
        result = fold.fold(CLEAN)
        self.assertEqual(len(result["rows"]), 1)

    def test_cannot_split_master(self):
        fold = session()
        fold.fold(CLEAN)
        with self.assertRaises(ValueError):
            fold.split("shop-77", ["shop-77"])


class ExportTest(unittest.TestCase):
    def test_export_whole_state(self):
        fold = session()
        fold.fold(CLEAN, domain="merchant")
        data = fold.export()
        self.assertEqual(len(data["groups"]), 1)
        json.dumps(data, ensure_ascii=False)  # 可序列化

    def test_export_single_record_includes_replay(self):
        fold = session()
        fold.fold(CLEAN)
        data = fold.export("s1-100")
        self.assertEqual(data["status"], "merged")
        self.assertTrue(data["replay"])

    def test_export_to_file(self):
        fold = session()
        fold.fold(CLEAN)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "fold.json")
            fold.export(path=path)
            with open(path, encoding="utf-8") as handle:
                self.assertIn("groups", json.load(handle))


class ConfigTest(unittest.TestCase):
    def test_invalid_threshold(self):
        with self.assertRaises(ValueError):
            RowFold(threshold=0)
        with self.assertRaises(ValueError):
            RowFold(threshold=1.5)

    def test_custom_key_fields(self):
        fold = session(
            key_fields=("name", "city"),
            similarity_fields=("name", "city", "phone"),
            completeness_fields=("name", "city", "phone", "contact"),
        )
        rows = [
            {"key": "x", "name": "甲店", "city": "上海", "amount": 5},
            {"key": "y", "name": "甲店", "city": "上海", "phone": "9", "amount": 5},
        ]
        result = fold.fold(rows)
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["masters"][0]["row"]["phone"], "9")

    def test_transitive_merge_is_explained(self):
        fold = session(
            similarity_fields=("name", "city", "phone"),
            min_shared=2,
            threshold=0.6,
        )
        rows = [
            {"key": "a", "name": "甲店", "city": "上海", "phone": "1", "amount": 1},
            {"key": "b", "name": "甲店", "city": "上海", "phone": "2", "amount": 1},
            {"key": "c", "name": "其他", "city": "上海", "phone": "2", "amount": 1},
        ]
        result = fold.fold(rows)
        self.assertEqual(len(result["rows"]), 1)
        bases = {reason["row_id"]: reason["basis"] for reason in result["reasons"]}
        self.assertIn("transitive", bases.values())
        transitive = [
            reason for reason in result["reasons"] if reason["basis"] == "transitive"
        ]
        self.assertIn("->", transitive[0]["decision"])

    def test_missing_key_rejected(self):
        with self.assertRaises(ValueError):
            session().fold([{"value": 1}])

    def test_non_dict_row_rejected(self):
        with self.assertRaises(TypeError):
            session().fold(["nope"])


if __name__ == "__main__":
    unittest.main()
