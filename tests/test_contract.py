import unittest

from dqcontract import (
    ContractError,
    check,
    drift_report,
    enforce,
    failure_samples,
    recheck,
    register_contract,
)

SCHEMA = {"version": 7, "fields": ["id", "name", "amount", "status"]}

CONTRACT = {
    "version": 3,
    "rules": [
        {"field": "id", "type": "not_null"},
        {"field": "id", "type": "unique"},
        {"field": "name", "type": "not_null"},
        {"field": "amount", "type": "range", "min": 0, "max": 10000},
        {"field": "status", "type": "enum",
         "values": ["new", "paid", "closed"], "severity": "warn"},
    ],
}


def approved(contract=None, schema=None):
    return register_contract(contract or CONTRACT, schema or SCHEMA, approved_by="ops")


class RegisterTest(unittest.TestCase):
    def test_change_requires_approval(self):
        with self.assertRaises(ContractError):
            register_contract(CONTRACT, SCHEMA)

    def test_register_binds_schema_version(self):
        contract = approved()
        self.assertEqual(contract["schema_version"], 7)
        self.assertEqual(contract["version"], 3)

    def test_rule_on_deleted_field_rejected(self):
        schema = {"version": 8, "fields": ["id", "name"]}
        with self.assertRaises(ContractError):
            register_contract(CONTRACT, schema, approved_by="ops")

    def test_invalid_rule_rejected(self):
        bad = {"version": 1, "rules": [{"field": "id", "type": "regexp"}]}
        with self.assertRaises(ContractError):
            register_contract(bad, SCHEMA, approved_by="ops")

    def test_unapproved_contract_cannot_check(self):
        with self.assertRaises(ContractError):
            check({"rows": []}, CONTRACT)


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.contract = approved()

    def test_all_required_fields_checked(self):
        result = check({"rows": [{"id": 1, "name": None}]}, self.contract)
        self.assertFalse(result["ok"])
        self.assertEqual(result["quarantined"], [0])

    def test_range_and_enum_and_unique(self):
        rows = [
            {"id": 1, "name": "a", "amount": 5, "status": "new"},
            {"id": 1, "name": "b", "amount": -3, "status": "???"},
            {"id": 2, "name": "c", "amount": 99999, "status": "paid"},
        ]
        result = check({"rows": rows}, self.contract)
        self.assertEqual(result["quarantined"], [1, 2])  # 重复 id、越界金额
        kinds = {(f["row"], f["rule"]) for f in result["failures"]}
        self.assertIn((1, "unique"), kinds)
        self.assertIn((1, "range"), kinds)
        self.assertIn((2, "range"), kinds)

    def test_warn_alerts_but_does_not_quarantine(self):
        rows = [{"id": 1, "name": "a", "amount": 1, "status": "???"}]
        result = check({"rows": rows}, self.contract)
        self.assertTrue(result["ok"])
        self.assertEqual(result["alerted"], [0])
        self.assertEqual(result["quarantined"], [])
        self.assertEqual(result["passed"], [0])

    def test_result_carries_versions_and_batch(self):
        result = check({"batch_id": "b1", "rows": [{"id": 1, "name": "a"}]}, self.contract)
        self.assertEqual(result["batch_id"], "b1")
        self.assertEqual(result["version"], 3)
        self.assertEqual(result["schema_version"], 7)

    def test_enforce_blocks_on_quarantine(self):
        bad = check({"rows": [{"id": None, "name": "a"}]}, self.contract)
        self.assertTrue(enforce(bad)["blocked"])
        good = check({"rows": [{"id": 1, "name": "a"}]}, self.contract)
        self.assertFalse(enforce(good)["blocked"])

    def test_failure_samples_by_batch(self):
        result = check({"batch_id": "b2", "rows": [{"id": None, "name": "a"}]}, self.contract)
        samples = failure_samples(result["batch_id"])
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["field"], "id")
        self.assertEqual(failure_samples("no-such-batch"), [])


class DriftTest(unittest.TestCase):
    def test_drift_reported_when_schema_changes(self):
        contract = approved()
        new_schema = {"version": 8, "fields": ["id", "amount", "status", "extra"]}
        result = check({"rows": [{"id": 1, "amount": 1}]}, contract, schema=new_schema)
        kinds = {d["kind"] for d in result["drift"]}
        self.assertIn("rule_on_missing_field", kinds)   # name 字段删了规则还在
        self.assertIn("field_without_rule", kinds)      # extra 字段无规则


class ShadowTest(unittest.TestCase):
    def test_shadow_rule_does_not_quarantine(self):
        contract = approved({
            "version": 4,
            "rules": [
                {"field": "id", "type": "not_null"},
                {"field": "amount", "type": "range", "min": 0, "mode": "shadow"},
            ],
        })
        rows = [{"id": 1, "amount": 5}, {"id": 2, "amount": -1}]
        result = check({"rows": rows}, contract)
        self.assertTrue(result["ok"])
        self.assertEqual(result["quarantined"], [])
        self.assertEqual(result["shadow"]["would_isolate"], 1)
        self.assertAlmostEqual(result["shadow"]["isolate_ratio"], 0.5)

    def test_shadow_samples_kept_for_review(self):
        contract = approved({
            "version": 4,
            "rules": [{"field": "amount", "type": "range", "min": 0, "mode": "shadow"}],
        })
        result = check({"batch_id": "shadow-1", "rows": [{"amount": -1}]}, contract)
        samples = failure_samples(result["batch_id"])
        self.assertEqual(samples[0]["mode"], "shadow")


class RecheckTest(unittest.TestCase):
    def test_repaired_rows_pass_recheck(self):
        contract = approved()
        rows = [{"id": None, "name": "a", "amount": 1, "status": "new"}]
        result = check({"rows": rows}, contract)
        self.assertEqual(result["quarantined"], [0])
        rows[0]["id"] = 1  # 修复隔离数据
        again = recheck(rows, contract)
        self.assertTrue(again["ok"])
        self.assertEqual(again["passed"], [0])


if __name__ == "__main__":
    unittest.main()
