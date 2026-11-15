import unittest

from crawlverify import (
    audit_log,
    quarantine,
    register_rule_version,
    reverify,
    rollback,
    verify,
)

SCHEMA = {
    "required": ["id", "title", "price", "start", "end"],
    "fields": {
        "id": {"type": "int", "min": 1},
        "title": {"type": "str"},
        "price": {"type": "number", "min": 0},
    },
    "rules": [{"name": "period", "left": "start", "op": "<=", "right": "end"}],
}


def good_row(**kw):
    row = {"id": 1, "title": "x", "price": 9.9, "start": 1, "end": 2, "site": "a"}
    row.update(kw)
    return row


class FieldCheckTest(unittest.TestCase):
    def test_wrong_type_isolated(self):
        result = verify([good_row(id="1")], SCHEMA)
        self.assertEqual(result["isolated"][0]["reason"], "type")
        self.assertEqual(result["isolated"][0]["failures"][0]["field"], "id")

    def test_bool_is_not_int(self):
        result = verify([good_row(id=True)], SCHEMA)
        self.assertEqual(result["isolated"][0]["reason"], "type")

    def test_range_violation_isolated(self):
        result = verify([good_row(price=-1)], SCHEMA)
        self.assertEqual(result["isolated"][0]["reason"], "range")

    def test_cross_field_rule_isolated(self):
        result = verify([good_row(start=5, end=2)], SCHEMA)
        self.assertEqual(result["isolated"][0]["reason"], "rule")
        self.assertEqual(result["isolated"][0]["failures"][0]["rule"], "period")

    def test_value_misplacement_detected(self):
        # 值错位：title 塞了数字、price 塞了字符串
        result = verify([good_row(title=123, price="9.9")], SCHEMA)
        failures = result["isolated"][0]["failures"]
        self.assertEqual({f["field"] for f in failures}, {"title", "price"})
        self.assertTrue(all(f["reason"] == "type" for f in failures))

    def test_rule_version_switch(self):
        row = good_row(id="1", price=-1)
        self.assertEqual(verify([row], dict(SCHEMA, version="v1"))["passed"], [0])
        self.assertEqual(len(verify([row], dict(SCHEMA, version="v2"))["isolated"]), 1)

    def test_register_rule_version(self):
        register_rule_version("v2-types-only",
                              {"types": True, "ranges": False, "rules": False})
        result = verify([good_row(price=-1)],
                        dict(SCHEMA, version="v2-types-only"))
        self.assertEqual(result["passed"], [0])

    def test_unknown_version_rejected(self):
        with self.assertRaises(ValueError):
            verify([good_row()], dict(SCHEMA, version="v9"))


class IsolationTest(unittest.TestCase):
    def test_quarantine_keeps_row_and_trace(self):
        result = verify([good_row(), good_row(price=-1)], SCHEMA)
        entries = quarantine(result["batch"])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["row"]["price"], -1)  # 原始行保留
        self.assertEqual(entries[0]["status"], "isolated")
        checks = [t["check"] for t in entries[0]["trace"]]
        self.assertIn("range", checks)  # 核对轨迹保留

    def test_good_rows_not_quarantined(self):
        result = verify([good_row(), good_row(price=-1)], SCHEMA)
        self.assertEqual(result["passed"], [0])
        self.assertEqual([e["index"] for e in quarantine(result["batch"])], [1])


class PartialRollbackTest(unittest.TestCase):
    def test_rollback_only_bad_rows(self):
        result = verify([good_row(), good_row(price=-1), good_row(id=2)], SCHEMA)
        out = rollback(result)
        self.assertEqual(out["rolled_back"], [1])
        self.assertEqual(out["kept"], [0, 2])

    def test_rollback_by_batch_id(self):
        result = verify([good_row(price=-1)], dict(SCHEMA, batch="b-x"))
        out = rollback("b-x")
        self.assertEqual(out["rolled_back"], [0])

    def test_rollback_writes_audit(self):
        before = len(audit_log())
        result = verify([good_row(), good_row(price=-1)], SCHEMA)
        rollback(result)
        log = audit_log()
        self.assertEqual(len(log), before + 1)
        self.assertEqual(log[-1]["batch"], result["batch"])
        self.assertEqual(log[-1]["rolled_back"], [1])
        self.assertIn("at", log[-1])

    def test_rolled_back_status_in_quarantine(self):
        result = verify([good_row(price=-1)], SCHEMA)
        rollback(result)
        self.assertEqual(quarantine(result["batch"])[0]["status"], "rolled_back")


class ReverifyTest(unittest.TestCase):
    def test_fix_then_reverify_releases_row(self):
        result = verify([good_row(price=-1)], SCHEMA)
        out = reverify(result["batch"], SCHEMA, fixes={0: {"price": 3.0}})
        self.assertEqual(out["released"], [0])
        self.assertEqual(out["remaining"], [])
        self.assertEqual(quarantine(result["batch"])[0]["status"], "passed")

    def test_reverify_keeps_trace_history(self):
        result = verify([good_row(price=-1)], SCHEMA)
        before = len(quarantine(result["batch"])[0]["trace"])
        reverify(result["batch"], SCHEMA, fixes={0: {"price": 3.0}})
        after = len(quarantine(result["batch"])[0]["trace"])
        self.assertGreater(after, before)

    def test_unfixed_row_stays(self):
        result = verify([good_row(price=-1)], SCHEMA)
        out = reverify(result["batch"], SCHEMA)
        self.assertEqual(out["remaining"], [0])


class SamplingTest(unittest.TestCase):
    def _rows(self):
        rows = [good_row(id=i, site="a") for i in range(1, 6)]
        rows += [good_row(id=i, site="b") for i in range(6, 11)]
        rows.append(good_row(id=11, price=-1, site="a"))   # range 错
        rows.append(good_row(id=12, title=1, site="b"))    # type 错
        return rows

    def test_sampling_stratified_by_site_and_error(self):
        result = verify(self._rows(), dict(SCHEMA, sample_rate=0.5, seed=7))
        strata = {s["stratum"] for s in result["sampled"]}
        self.assertIn("passed|a", strata)
        self.assertIn("passed|b", strata)
        self.assertIn("isolated|range", strata)
        self.assertIn("isolated|type", strata)

    def test_sampling_ratio(self):
        result = verify(self._rows(), dict(SCHEMA, sample_rate=0.5, seed=7))
        passed_a = [s for s in result["sampled"] if s["stratum"] == "passed|a"]
        self.assertEqual(len(passed_a), 3)  # ceil(5 * 0.5)

    def test_sampling_deterministic_with_seed(self):
        r1 = verify(self._rows(), dict(SCHEMA, sample_rate=0.5, seed=7))
        r2 = verify(self._rows(), dict(SCHEMA, sample_rate=0.5, seed=7))
        self.assertEqual(r1["sampled"], r2["sampled"])

    def test_zero_rate_no_sampling(self):
        result = verify(self._rows(), dict(SCHEMA, sample_rate=0))
        self.assertEqual(result["sampled"], [])


if __name__ == "__main__":
    unittest.main()
