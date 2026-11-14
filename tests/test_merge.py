import unittest

from fieldmerge import configure, merge, reset
from fieldmerge import merge as module


class MergeTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_later_source_wins(self):
        row = merge("r1", {"a": {"v": 1}, "b": {"v": 2}})
        self.assertEqual(row["v"], 2)

    def test_cross_source_mixing(self):
        row = merge("r1", {"a": {"x": 1}, "b": {"y": 2}})
        self.assertEqual(set(row), {"x", "y"})

    def test_no_attribution(self):
        self.assertNotIn("source", merge("r1", {"a": {"v": 1}}))


class RowConsistentMergeTest(unittest.TestCase):
    def setUp(self):
        reset()
        configure()

    def test_winning_source_owns_whole_row(self):
        result = merge("r1", {
            "erp": {"$confidence": 0.9, "fields": {"amount": 100, "ccy": "USD"}},
            "crm": {"$confidence": 0.4, "fields": {"amount": 120, "owner": "Ada"}},
        })
        self.assertEqual(result["amount"], 100)
        self.assertEqual(result["ccy"], "USD")
        self.assertEqual(result["owner"], "Ada")
        self.assertEqual(result.winning_source, "erp")
        conflict = next(c for c in result.conflicts if c["type"] == "field_conflict")
        self.assertEqual(conflict["field"], "amount")
        self.assertEqual(conflict["winner_source"], "erp")

    def test_confidence_weight_picks_source(self):
        result = merge("r1", {
            "weak": {"$confidence": 0.3, "v": 1},
            "strong": {"$confidence": 0.8, "v": 2},
        })
        self.assertEqual(result["v"], 2)
        self.assertEqual(result.winning_source, "strong")
        self.assertGreater(result.sources["strong"]["weight"],
                           result.sources["weak"]["weight"])
        self.assertFalse(any(r["rule"] == "time_decay" for r in result.rules))

    def test_time_decay_lowers_old_source_weight(self):
        result = merge("r1", {
            "old": {"$confidence": 1.0, "$timestamp": 0.0, "v": 1},
            "new": {"$confidence": 1.0, "$timestamp": 3600.0, "v": 2},
        })
        old = result.sources["old"]
        self.assertAlmostEqual(old["decay"], 0.5)
        self.assertAlmostEqual(old["weight"], 0.5)
        self.assertAlmostEqual(result.sources["new"]["decay"], 1.0)
        self.assertEqual(result.winning_source, "new")
        self.assertEqual(result["v"], 2)
        decay_rules = [r for r in result.rules if r["rule"] == "time_decay"]
        self.assertTrue(decay_rules)
        self.assertEqual(decay_rules[0]["half_life"], 3600.0)

    def test_equal_weight_keeps_all_candidates_and_flags_conflict(self):
        result = merge("r1", {
            "s1": {"$confidence": 0.5, "v": "a"},
            "s2": {"$confidence": 0.5, "v": "b"},
        })
        self.assertEqual(result.winning_source, "s2")
        self.assertEqual(result["v"], "b")
        self.assertTrue(result.has_conflict)
        tie = next(c for c in result.conflicts if c["type"] == "source_tie")
        self.assertEqual(tie["sources"], ["s1", "s2"])
        self.assertEqual(tie["winner"], "s2")
        self.assertEqual({c["source"] for c in result.candidates["v"]},
                         {"s1", "s2"})

    def test_explainable_output_carries_candidates_and_rules(self):
        result = merge("r1", {
            "erp": {"$confidence": 0.9, "$timestamp": 100.0,
                    "fields": {"amount": 100, "ccy": "USD"}},
            "crm": {"$confidence": 0.4, "$timestamp": 200.0,
                    "fields": {"amount": 120, "stage": "won"}},
        })
        self.assertEqual(set(result.candidates), {"amount", "ccy", "stage"})
        amount = result.candidates["amount"]
        self.assertEqual(len(amount), 2)
        self.assertEqual({c["source"] for c in amount}, {"erp", "crm"})
        self.assertTrue(all("weight" in c and "confidence" in c
                            for c in amount))
        rule_names = {r["rule"] for r in result.rules}
        self.assertIn("source_scored", rule_names)
        self.assertIn("winner_selected", rule_names)
        self.assertIn("field_conflict", rule_names)
        winner_rule = next(r for r in result.rules
                           if r["rule"] == "winner_selected")
        self.assertEqual(winner_rule["source"], "erp")
        self.assertEqual(result.weight, result.sources["erp"]["weight"])

    def test_repeated_calls_accumulate_sources_per_row(self):
        merge("r1", {"a": {"$confidence": 0.2, "v": 1}})
        result = merge("r1", {"b": {"$confidence": 0.9, "v": 2}})
        self.assertEqual(result.winning_source, "b")
        self.assertEqual({s for s in result.sources}, {"a", "b"})
        self.assertEqual(len(result.candidates["v"]), 2)

    def test_configure_changes_half_life(self):
        configure(half_life=7200.0)
        result = merge("r1", {
            "old": {"$confidence": 1.0, "$timestamp": 0.0, "v": 1},
            "new": {"$confidence": 1.0, "$timestamp": 3600.0, "v": 2},
        })
        self.assertAlmostEqual(result.sources["old"]["decay"], 0.5 ** 0.5)

    def test_legacy_payload_uses_default_confidence(self):
        result = merge("r1", {
            "a": {"v": 1},
            "b": {"v": 2},
        })
        self.assertEqual(result.sources["a"]["confidence"], 1.0)
        self.assertEqual(result.sources["a"]["weight"], 1.0)
        self.assertEqual(result.winning_source, "b")
