"""审计归档：按操作人和时间（UTC 日期）落盘，可双向检索。"""

import json
import os
import tempfile
import unittest

from tabledesens import AuditLog, DesensEngine, Lineage, MASK


class AuditArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit_dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _engine(self):
        lineage = Lineage(
            edges=[("ods.t", "dwd.t")],
            rules={"ods.t": {"phone": MASK}})
        return DesensEngine(lineage, node="dwd.t",
                            audit=AuditLog(self.audit_dir))

    def test_archive_partitioned_by_date_and_operator(self):
        engine = self._engine()
        result = engine.mask({"phone": "13800000000"}, operator="etl-bot")
        engine.permission.grant("analyst", "dwd.t")
        engine.reveal(result.row, result.tokens, operator="analyst")

        day = result.audit["ts"][:10]  # YYYY-MM-DD
        etl_dir = os.path.join(self.audit_dir, day, "etl-bot")
        analyst_dir = os.path.join(self.audit_dir, day, "analyst")
        self.assertTrue(os.path.isdir(etl_dir))
        self.assertTrue(os.path.isdir(analyst_dir))

        etl_files = os.listdir(etl_dir)
        analyst_files = os.listdir(analyst_dir)
        self.assertEqual(len(etl_files), 1)
        self.assertEqual(len(analyst_files), 1)

        with open(os.path.join(analyst_dir, analyst_files[0]),
                  encoding="utf-8") as fh:
            event = json.load(fh)
        self.assertEqual(event["action"], "reveal")
        self.assertEqual(event["operator"], "analyst")
        self.assertEqual(event["node"], "dwd.t")
        self.assertIn("phone", event["fields"])

    def test_query_filters(self):
        audit = AuditLog(self.audit_dir)
        audit.record("mask", "alice", "n1", strategies={})
        audit.record("mask", "bob", "n1", strategies={})
        audit.record("reveal", "alice", "n2", fields=[])

        self.assertEqual(len(audit.query(operator="alice")), 2)
        self.assertEqual(len(audit.query(operator="bob")), 1)
        self.assertEqual(len(audit.query(action="reveal")), 1)
        self.assertEqual(len(audit.query(node="n1")), 2)

    def test_denied_reveal_is_archived(self):
        engine = self._engine()
        result = engine.mask({"phone": "13800000000"}, operator="etl-bot")
        with self.assertRaises(Exception):
            engine.reveal(result.row, result.tokens, operator="intruder")

        events = engine.audit.query(operator="intruder")
        self.assertEqual(events[0]["action"], "reveal_denied")
        # 拒绝事件同样按操作人归档
        day = events[0]["ts"][:10]
        self.assertTrue(
            os.path.isdir(os.path.join(
                self.audit_dir, day, "intruder")))

    def test_in_memory_log_without_dir(self):
        audit = AuditLog()
        event = audit.record("mask", "etl", "n")
        self.assertIn(event, audit.events)
        self.assertIsNone(audit.audit_dir)

    def test_anonymous_operator_normalized(self):
        audit = AuditLog(self.audit_dir)
        event = audit.record("mask", None, "n")
        self.assertEqual(event["operator"], "anonymous")
        day = event["ts"][:10]
        self.assertTrue(
            os.path.isdir(os.path.join(
                self.audit_dir, day, "anonymous")))


if __name__ == "__main__":
    unittest.main()
