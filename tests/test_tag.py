import importlib
import unittest
from datetime import datetime, timedelta, timezone

from retentiontag import (
    apply,
    audit,
    effective,
    exempt,
    lease,
    link,
    propagation,
    release,
    tag,
)

module = importlib.import_module("retentiontag.tag")


class TagTest(unittest.TestCase):
    def setUp(self):
        module._tags.clear()
        module._lineage.clear()
        module._exemptions.clear()
        module._leases.clear()
        module._audit.clear()
        module._effective.clear()
        module._sources.clear()
        module._propagation.clear()

    def test_tag_stores(self):
        self.assertEqual(tag("t", "keep30")["tag"], "keep30")

    def test_tag_overwrites(self):
        tag("t", "keep30")
        tag("t", "keep7")
        self.assertEqual(module._tags["t"], "keep7")

    def test_apply_counts(self):
        tag("t", "keep7")
        self.assertEqual(apply("t")["deleted"], 1)


class InheritanceTest(unittest.TestCase):
    def setUp(self):
        module.reset()

    def test_downstream_inherits_from_upstream(self):
        link("orders", "order_items")
        tag("orders", "keep30")
        self.assertEqual(effective("order_items"), "keep30")
        result = tag("orders", "keep30")
        propagated = {record["table"] for record in result["propagated"]}
        self.assertIn("order_items", propagated)

    def test_inheritance_is_transitive(self):
        link("orders", "order_items")
        link("order_items", "order_report")
        tag("orders", "keep30")
        self.assertEqual(effective("order_report"), "keep30")

    def test_propagation_records_source_layer(self):
        link("orders", "order_items")
        link("order_items", "order_report")
        tag("orders", "keep30")
        records = {record["table"]: record for record in propagation()}
        self.assertEqual(records["order_items"]["source"], "orders")
        self.assertEqual(records["order_items"]["layer"], 1)
        self.assertEqual(records["order_report"]["source"], "orders")
        self.assertEqual(records["order_report"]["layer"], 2)

    def test_downstream_may_tighten(self):
        link("orders", "order_items")
        tag("orders", "keep30")
        tag("order_items", "keep7")
        self.assertEqual(effective("orders"), "keep30")
        self.assertEqual(effective("order_items"), "keep7")

    def test_downstream_cannot_loosen(self):
        link("orders", "order_items")
        tag("orders", "keep7")
        result = tag("order_items", "keep30")
        self.assertEqual(effective("order_items"), "keep7")
        self.assertEqual(result["effective"], "keep7")
        rejected = [
            record
            for record in result["propagated"]
            if record["table"] == "order_items" and record.get("rejected")
        ]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["rejected"], "keep30")

    def test_upstream_relaxation_still_bounded_by_other_parent(self):
        link("orders", "order_items")
        link("inventory", "order_items")
        tag("orders", "keep7")
        tag("inventory", "keep30")
        self.assertEqual(effective("order_items"), "keep7")

    def test_tag_change_is_audited(self):
        link("orders", "order_items")
        tag("orders", "keep30")
        tag("orders", "keep7")
        entries = audit()
        self.assertEqual(entries[0]["old"], None)
        self.assertEqual(entries[0]["new"], "keep30")
        self.assertEqual(entries[1]["old"], "keep30")
        self.assertEqual(entries[1]["new"], "keep7")
        tag("orders", "keep7")
        self.assertEqual(len(audit()), 2)


class ExemptionTest(unittest.TestCase):
    def setUp(self):
        module.reset()

    def _expiry(self, days):
        return datetime.now(timezone.utc) + timedelta(days=days)

    def test_exemption_requires_approver(self):
        with self.assertRaises(ValueError):
            exempt("orders", self._expiry(7), "")

    def test_active_exemption_skips_table(self):
        link("orders", "order_items")
        tag("orders", "keep7")
        exempt("orders", self._expiry(7), "alice")
        result = apply("orders")
        self.assertIn(
            {"table": "orders", "reason": "active exemption", "approver": "alice"},
            result["skipped"],
        )
        self.assertNotIn("orders", [t for b in result["batches"] for t in b["tables"]])

    def test_expired_exemption_is_ignored(self):
        link("orders", "order_items")
        tag("orders", "keep7")
        exempt("orders", self._expiry(-1), "alice")
        result = apply("orders")
        self.assertEqual(result["exemptions"][0]["status"], "expired")
        self.assertEqual(result["deleted"], 2)

    def test_exemption_output_reports_status(self):
        link("orders", "order_items")
        tag("orders", "keep7")
        exempt("orders", self._expiry(7), "alice", "quarter close")
        result = apply("orders")
        record = result["exemptions"][0]
        self.assertEqual(record["table"], "orders")
        self.assertEqual(record["status"], "active")
        self.assertEqual(record["approver"], "alice")

    def test_active_lease_skips_table(self):
        link("orders", "order_items")
        tag("orders", "keep7")
        lease("orders", self._expiry(7))
        result = apply("orders")
        self.assertIn(
            {"table": "orders", "reason": "active lease"}, result["skipped"]
        )
        release("orders")
        self.assertEqual(apply("orders")["deleted"], 2)


class CascadeTest(unittest.TestCase):
    def setUp(self):
        module.reset()

    def test_deletes_dependents_before_parent(self):
        link("orders", "order_items")
        link("orders", "invoices")
        link("order_items", "order_report")
        tag("orders", "keep7")
        result = apply("orders")
        self.assertEqual(
            result["dependency_order"],
            ["order_report", "invoices", "order_items", "orders"],
        )

    def test_batches_follow_dependency_depth(self):
        link("orders", "order_items")
        link("order_items", "order_report")
        tag("orders", "keep7")
        result = apply("orders")
        self.assertEqual(len(result["batches"]), 3)
        self.assertEqual(result["batches"][0]["tables"], ["order_report"])
        self.assertEqual(result["batches"][1]["tables"], ["order_items"])
        self.assertEqual(result["batches"][2]["tables"], ["orders"])
        self.assertEqual(result["batches"][2]["deleted"], 1)

    def test_retained_child_blocks_parent(self):
        link("orders", "order_items")
        tag("orders", "keep7")
        exempt("order_items", datetime.now(timezone.utc) + timedelta(days=7), "bob")
        result = apply("orders")
        self.assertEqual(result["deleted"], 0)
        blocked = next(
            skip for skip in result["skipped"] if skip["table"] == "orders"
        )
        self.assertEqual(blocked["reason"], "blocked by retained dependent")
        self.assertEqual(blocked["blocked_by"], "order_items")

    def test_cascade_outputs_effective_tags(self):
        link("orders", "order_items")
        link("order_items", "order_report")
        tag("orders", "keep30")
        tag("order_report", "keep7")
        result = apply("orders")
        self.assertEqual(
            result["effective"],
            {
                "orders": "keep30",
                "order_items": "keep30",
                "order_report": "keep7",
            },
        )
        self.assertEqual(result["tag"], "keep30")


if __name__ == "__main__":
    unittest.main()
