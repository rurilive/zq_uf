import json
import unittest

from schemagate import apply, check


class GateTest(unittest.TestCase):
    def test_check_allows_everything(self):
        self.assertTrue(check({"a": "int"}, {"a": "text"})["allowed"])

    def test_check_has_no_breaking(self):
        self.assertEqual(check({}, {})["breaking"], [])

    def test_apply_has_no_gray(self):
        self.assertEqual(apply({"tables": ["t1"]})["gray"], [])


class GateGrayTest(unittest.TestCase):
    def test_drop_column_blocked_with_suggestion(self):
        r = check({"a": "int", "b": "text"}, {"a": "int"})
        self.assertFalse(r["allowed"])
        self.assertEqual(len(r["breaking"]), 1)
        self.assertTrue(r["breaking"][0]["suggestion"])

    def test_narrow_type_blocked(self):
        self.assertFalse(check({"a": "text"}, {"a": "int"})["allowed"])

    def test_compatible_change_audited(self):
        r = check({"a": "int"}, {"a": "int", "b": "text"})
        self.assertTrue(r["allowed"])
        self.assertEqual(len(r["compatible"]), 1)
        self.assertTrue(r["audit"])

    def test_check_result_exportable(self):
        data = json.loads(check({"a": "int"}, {"a": "text"})["export"])
        self.assertTrue(data["allowed"])
        self.assertIn("summary", data)

    def test_version_monotonic(self):
        self.assertLess(check({}, {})["version"], check({}, {})["version"])

    def test_apply_blocked_on_breaking(self):
        r = check({"a": "int"}, {})
        out = apply({"tables": ["br1"], "check": r, "approver": "ops", "reason": "清理"})
        self.assertTrue(out["blocked"])
        self.assertEqual(out["applied"], [])
        block = [e for e in out["audit"] if e["action"] == "block"][0]
        self.assertTrue(block["suggestions"])

    def test_gray_rollout_by_ratio(self):
        r = check({"a": "int"}, {"a": "int", "b": "text"})
        out = apply({"tables": ["g1", "g2", "g3", "g4"], "check": r,
                     "gray_ratio": 0.5, "approver": "ops", "reason": "加列"})
        self.assertEqual(out["gray"], ["g1", "g2"])
        self.assertEqual(sorted(out["applied"]), ["g1", "g2", "g3", "g4"])
        self.assertEqual(out["rolled_back"], [])
        self.assertEqual(out["coexist"], {"new": ["g1", "g2"], "old": ["g3", "g4"]})

    def test_gray_failure_rolls_back_to_previous_version(self):
        prev = apply({"tables": ["r1", "r2"]})["version"]
        r = check({"a": "int"}, {"a": "float"})
        out = apply({"tables": ["r1", "r2"], "check": r, "gray_ratio": 0.5,
                     "fail_tables": ["r1"], "approver": "ops", "reason": "放宽"})
        self.assertEqual(out["rolled_back"], ["r1"])
        self.assertEqual(out["applied"], [])
        rollback = [e for e in out["audit"] if e["action"] == "rollback"][0]
        self.assertEqual(rollback["restored"]["r1"], prev)

    def test_approval_audit_has_summary(self):
        r = check({"a": "int"}, {"a": "int", "b": "text"})
        out = apply({"tables": ["a1"], "check": r,
                     "approver": "ops", "reason": "需求123"})
        approval = [e for e in out["audit"] if e["action"] == "approve"][0]
        self.assertEqual(approval["approver"], "ops")
        self.assertEqual(approval["reason"], "需求123")
        self.assertTrue(approval["summary"])
