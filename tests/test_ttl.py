import unittest

from tablettl import (
    apply,
    archive_manifest,
    plan,
    reset_state,
    restore,
    restore_status,
    table_data,
    validate_archive,
    verify_restore,
)


def part(name, age_days=40, rows=None, **extra):
    payload = {"name": name, "age_days": age_days, "rows": rows if rows is not None else []}
    payload.update(extra)
    return payload


class TtlTest(unittest.TestCase):
    def test_plan_selects_old(self):
        result = plan("t", [{"name": "p1", "age_days": 40}, {"name": "p2", "age_days": 2}])
        self.assertEqual([p["name"] for p in result["expire"]], ["p1"])

    def test_apply_counts(self):
        self.assertEqual(apply({"expire": [{"name": "p1"}]})["deleted"], 1)

    def test_apply_skips_empty(self):
        self.assertEqual(apply({"expire": []})["skipped"], [])


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        reset_state()

    def test_archive_before_delete_with_manifest(self):
        rows = [{"id": 1}, {"id": 2}, {"id": 3}]
        p = plan("t", [part("p1", rows=rows)])
        result = apply(p)

        manifest = archive_manifest("t", "p1")
        self.assertEqual(manifest["row_count"], 3)
        self.assertTrue(validate_archive(manifest))
        self.assertEqual(result["archived_count"], 1)
        self.assertEqual(result["archived"][0]["row_count"], 3)
        self.assertEqual(result["deleted"], 1)
        self.assertIsNone(table_data("t", "p1"))

    def test_archive_failure_skips_delete(self):
        p = plan("t", [part("p1", rows=[{"id": 1}], archive_fails=True)])
        result = apply(p)

        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["archived_count"], 0)
        self.assertEqual(result["skipped"][0]["reason"], "archive_failed")
        self.assertIsNotNone(table_data("t", "p1"))

    def test_validate_archive_detects_tampering(self):
        apply(plan("t", [part("p1", rows=[{"id": 1}])]))
        manifest = archive_manifest("t", "p1")
        manifest["rows"].append({"id": 2})
        self.assertFalse(validate_archive(manifest))
        manifest["rows"].pop()
        manifest["checksum"] = "bad"
        self.assertFalse(validate_archive(manifest))


class DownstreamTest(unittest.TestCase):
    def setUp(self):
        reset_state()

    def test_unconfirmed_is_skipped_and_recorded(self):
        p = plan(
            "t",
            [
                part("p1", rows=[{"id": 1}], downstream_confirmed=False),
                part("p2", rows=[{"id": 2}], downstream_confirmed=True),
            ],
        )
        result = apply(p)

        self.assertEqual(result["deleted"], 1)
        self.assertIn(
            {"name": "p1", "reason": "downstream_unconfirmed"}, result["skipped"]
        )
        self.assertIsNotNone(table_data("t", "p1"))
        self.assertIsNone(table_data("t", "p2"))
        self.assertIsNone(archive_manifest("t", "p1"))


class BatchTest(unittest.TestCase):
    def setUp(self):
        reset_state()

    def test_delete_in_batches(self):
        partitions = [part(f"p{i}", rows=[{"id": i}]) for i in range(5)]
        result = apply(plan("t", partitions, batch_size=2))

        self.assertEqual([b["deleted"] for b in result["batches"]], [2, 2, 1])
        self.assertEqual([b["partitions"] for b in result["batches"]], [
            ["p0", "p1"], ["p2", "p3"], ["p4"]
        ])
        self.assertEqual(result["deleted"], 5)
        self.assertEqual(result["archived_count"], 5)

    def test_batch_failure_stops(self):
        partitions = [part(f"p{i}", rows=[{"id": i}]) for i in range(5)]
        partitions[2]["delete_fails"] = True
        result = apply(plan("t", partitions, batch_size=2))

        self.assertEqual(result["batches"][0]["status"], "ok")
        self.assertEqual(result["batches"][1]["status"], "failed")
        self.assertEqual(result["batches"][1]["failed"], "p2")
        self.assertTrue(result["stopped"])
        self.assertEqual(result["deleted"], 2)
        self.assertEqual(
            [(s["name"], s["reason"]) for s in result["skipped"]],
            [("p2", "delete_failed"), ("p3", "delete_stopped"), ("p4", "delete_stopped")],
        )
        self.assertIsNotNone(table_data("t", "p2"))
        self.assertIsNotNone(table_data("t", "p3"))
        self.assertIsNotNone(table_data("t", "p4"))


class RestoreTest(unittest.TestCase):
    def setUp(self):
        reset_state()

    def test_restore_verifies_by_partition(self):
        rows = [{"id": 1}, {"id": 2}]
        apply(plan("t", [part("p1", rows=rows), part("p2", rows=[{"id": 3}])]))

        results = restore("t")
        self.assertEqual([r["partition"] for r in results], ["p1", "p2"])
        self.assertTrue(all(r["verified"] for r in results))

        self.assertEqual(restore_status("t", "p1")["status"], "restored")
        self.assertEqual(restore_status("t", "p1")["row_count"], 2)
        self.assertEqual(restore_status("t", "missing"), None)

        check = verify_restore("t", "p1")
        self.assertTrue(check["verified"])
        self.assertEqual(table_data("t", "p1")["rows"], rows)
        self.assertTrue(all(c["verified"] for c in verify_restore("t")))

    def test_restore_failure_keeps_partition(self):
        apply(plan("t", [part("p1", rows=[{"id": 1}, {"id": 2}], restore_fails=True)]))
        existing = plan("t", [part("p1", age_days=1, rows=[{"id": 9}])])
        self.assertEqual(existing["expire"], [])

        result = restore("t", "p1")[0]
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["verified"])
        self.assertEqual(result["reason"], "restore_verify_failed")
        self.assertEqual(table_data("t", "p1")["rows"], [{"id": 9}])
        self.assertIsNotNone(archive_manifest("t", "p1"))
        self.assertFalse(verify_restore("t", "p1")["verified"])

    def test_restore_missing_archive(self):
        result = restore("t", "p1")[0]
        self.assertEqual(result["reason"], "archive_missing")
        self.assertIsNone(table_data("t", "p1"))


if __name__ == "__main__":
    unittest.main()
