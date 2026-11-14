import unittest

from rowmerge import (
    audit,
    discards,
    lookup,
    merge,
    report,
    reset,
    set_source_priority,
)


class MergeTest(unittest.TestCase):
    def setUp(self):
        reset(include_priority=True)

    def test_later_wins(self):
        result = merge([{"id": 1, "v": 2}, {"id": 1, "v": 1}])
        self.assertEqual(result["rows"][1]["v"], 1)

    def test_no_conflicts(self):
        self.assertEqual(merge([{"id": 1}])["conflicts"], [])

    def test_report_empty(self):
        self.assertEqual(report(), [])


class VersionVectorTest(unittest.TestCase):
    def setUp(self):
        reset(include_priority=True)

    def test_new_version_then_old_is_not_overwritten(self):
        merge(
            [
                {
                    "id": 10,
                    "v": "new",
                    "version": {"a": 2, "b": 1},
                    "updated_at": 200,
                    "source": "a",
                }
            ]
        )
        result = merge(
            [
                {
                    "id": 10,
                    "v": "old",
                    "version": {"a": 1, "b": 1},
                    "updated_at": 100,
                    "source": "a",
                }
            ]
        )
        self.assertEqual(result["rows"][10]["v"], "new")
        self.assertEqual([row["reason"] for row in result["discards"]], ["superseded_by_newer"])

    def test_old_then_new_takes_new(self):
        merge(
            [
                {
                    "id": 11,
                    "v": "old",
                    "version": {"a": 1},
                    "updated_at": 100,
                    "source": "a",
                }
            ]
        )
        result = merge(
            [
                {
                    "id": 11,
                    "v": "new",
                    "version": {"a": 2},
                    "updated_at": 200,
                    "source": "a",
                }
            ]
        )
        self.assertEqual(result["rows"][11]["v"], "new")
        self.assertEqual(result["conflicts"], [])

    def test_component_wise_compare_not_total_count(self):
        # 两边的计数总和相同，但分量互不包含：并发，不能判成"旧版本"。
        merge(
            [
                {
                    "id": 12,
                    "v": "from-a",
                    "version": {"a": 1},
                    "updated_at": 100,
                    "source": "a",
                }
            ]
        )
        result = merge(
            [
                {
                    "id": 12,
                    "v": "from-b",
                    "version": {"b": 1},
                    "updated_at": 200,
                    "source": "b",
                }
            ]
        )
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(result["conflicts"][0]["reason"], "concurrent")
        self.assertEqual(result["rows"][12]["v"], "from-b")

    def test_batch_order_converges(self):
        first = {
            "id": 13,
            "v": "from-a",
            "version": {"a": 2, "b": 1},
            "updated_at": 100,
            "source": "a",
        }
        second = {
            "id": 13,
            "v": "from-b",
            "version": {"a": 1, "b": 2},
            "updated_at": 200,
            "source": "b",
        }
        forward = merge([first])
        forward = merge([second], batch="forward")
        backward = merge([second])
        backward = merge([first], batch="backward")
        self.assertEqual(forward["rows"][13]["v"], backward["rows"][13]["v"])
        self.assertEqual(forward["rows"][13]["v"], "from-b")

    def test_reapplying_same_batch_keeps_state(self):
        row = {
            "id": 14,
            "v": "x",
            "version": {"a": 1},
            "updated_at": 100,
            "source": "a",
        }
        merge([row])
        result = merge([row])
        self.assertEqual(result["rows"][14]["v"], "x")
        self.assertEqual([item["reason"] for item in result["discards"]], ["duplicate"])
        self.assertEqual(result["conflicts"], [])


class TombstoneTest(unittest.TestCase):
    def setUp(self):
        reset(include_priority=True)

    def test_old_update_does_not_resurrect_delete(self):
        merge(
            [
                {
                    "id": 20,
                    "v": "alive",
                    "version": {"a": 1},
                    "updated_at": 100,
                    "source": "a",
                }
            ]
        )
        deleted = merge(
            [
                {
                    "id": 20,
                    "deleted": True,
                    "version": {"a": 1, "b": 1},
                    "updated_at": 200,
                    "source": "b",
                }
            ]
        )
        self.assertTrue(deleted["rows"][20]["deleted"])
        stale = merge(
            [
                {
                    "id": 20,
                    "v": "stale-update",
                    "version": {"a": 1},
                    "updated_at": 150,
                    "source": "a",
                }
            ]
        )
        self.assertTrue(stale["rows"][20]["deleted"])
        self.assertEqual([row["reason"] for row in stale["discards"]], ["tombstoned"])

    def test_concurrent_update_does_not_beat_tombstone(self):
        merge(
            [
                {
                    "id": 21,
                    "v": "alive",
                    "version": {"a": 1, "b": 1},
                    "updated_at": 100,
                    "source": "a",
                }
            ]
        )
        merge(
            [
                {
                    "id": 21,
                    "deleted": True,
                    "version": {"a": 1, "b": 2},
                    "updated_at": 200,
                    "source": "b",
                }
            ]
        )
        result = merge(
            [
                {
                    "id": 21,
                    "v": "concurrent-update",
                    "version": {"a": 2, "b": 1},
                    "updated_at": 300,
                    "source": "a",
                }
            ]
        )
        self.assertTrue(result["rows"][21]["deleted"])
        self.assertEqual(result["conflicts"][0]["resolution"], "tombstone")

    def test_concurrent_delete_wins_even_from_lower_priority_source(self):
        # 删除优先于并发更新，且不受来源优先级影响。
        set_source_priority({"a": 10, "b": 1})
        merge(
            [
                {
                    "id": 23,
                    "v": "alive",
                    "version": {"repo": 1},
                    "updated_at": 100,
                    "source": "a",
                }
            ]
        )
        merge(
            [
                {
                    "id": 23,
                    "v": "from-a",
                    "version": {"repo": 1, "a": 1},
                    "updated_at": 110,
                    "source": "a",
                }
            ]
        )
        result = merge(
            [
                {
                    "id": 23,
                    "deleted": True,
                    "version": {"repo": 1, "b": 1},
                    "updated_at": 105,
                    "source": "b",
                }
            ]
        )
        self.assertTrue(result["rows"][23]["deleted"])
        self.assertEqual(result["conflicts"][0]["resolution"], "tombstone")

    def test_newer_version_can_resurrect(self):
        merge(
            [
                {
                    "id": 22,
                    "deleted": True,
                    "version": {"a": 1, "b": 1},
                    "updated_at": 100,
                    "source": "a",
                }
            ]
        )
        result = merge(
            [
                {
                    "id": 22,
                    "v": "recreated",
                    "version": {"a": 1, "b": 2},
                    "updated_at": 200,
                    "source": "b",
                }
            ]
        )
        self.assertFalse(result["rows"][22].get("deleted"))
        self.assertEqual(result["rows"][22]["v"], "recreated")
        self.assertEqual(result["conflicts"], [])


class SourcePriorityTest(unittest.TestCase):
    def setUp(self):
        reset(include_priority=True)

    def test_same_version_higher_priority_wins(self):
        set_source_priority({"a": 1, "b": 5})
        merge(
            [
                {
                    "id": 30,
                    "v": "from-a",
                    "version": {"a": 1, "b": 1},
                    "updated_at": 100,
                    "source": "a",
                }
            ]
        )
        result = merge(
            [
                {
                    "id": 30,
                    "v": "from-b",
                    "version": {"a": 1, "b": 1},
                    "updated_at": 100,
                    "source": "b",
                }
            ]
        )
        self.assertEqual(result["rows"][30]["v"], "from-b")
        self.assertEqual(result["conflicts"][0]["reason"], "same_version")
        self.assertEqual(result["conflicts"][0]["resolution"], "source_priority")

    def test_priority_can_backfill_late_arrival(self):
        # 低优先级先到，高优先级后到且同版本：高优先级必须仍然覆盖。
        set_source_priority({"a": 1, "b": 5})
        merge(
            [
                {
                    "id": 31,
                    "v": "from-b",
                    "version": {"a": 1, "b": 1},
                    "updated_at": 100,
                    "source": "b",
                }
            ]
        )
        result = merge(
            [
                {
                    "id": 31,
                    "v": "from-a",
                    "version": {"a": 1, "b": 1},
                    "updated_at": 100,
                    "source": "a",
                }
            ]
        )
        self.assertEqual(result["rows"][31]["v"], "from-b")
        self.assertEqual([row["reason"] for row in result["discards"]], ["conflict_loser"])

    def test_per_call_priority_overrides_registry(self):
        set_source_priority({"a": 9})
        merge(
            [
                {
                    "id": 32,
                    "v": "from-a",
                    "version": {"a": 1},
                    "updated_at": 100,
                    "source": "a",
                }
            ]
        )
        result = merge(
            [
                {
                    "id": 32,
                    "v": "from-b",
                    "version": {"a": 1},
                    "updated_at": 100,
                    "source": "b",
                }
            ],
            source_priority={"b": 100},
        )
        self.assertEqual(result["rows"][32]["v"], "from-b")

    def test_unregistered_source_defaults_to_zero(self):
        set_source_priority({"a": 1}, replace=True)
        merge(
            [
                {
                    "id": 33,
                    "v": "from-a",
                    "version": {"a": 1},
                    "updated_at": 100,
                    "source": "a",
                }
            ]
        )
        result = merge(
            [
                {
                    "id": 33,
                    "v": "from-zzz",
                    "version": {"a": 1},
                    "updated_at": 100,
                    "source": "zzz",
                }
            ]
        )
        self.assertEqual(result["rows"][33]["v"], "from-a")


class ConflictReportTest(unittest.TestCase):
    def setUp(self):
        reset(include_priority=True)

    def _concurrent_pair(self):
        merge(
            [
                {
                    "id": 40,
                    "v": "left",
                    "version": {"a": 1},
                    "updated_at": 100,
                    "source": "a",
                }
            ]
        )
        return merge(
            [
                {
                    "id": 40,
                    "v": "right",
                    "version": {"b": 1},
                    "updated_at": 200,
                    "source": "b",
                }
            ]
        )

    def test_conflict_keeps_both_sides(self):
        result = self._concurrent_pair()
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["key"], 40)
        self.assertEqual(len(conflict["values"]), 2)
        self.assertEqual(
            sorted(row["v"] for row in conflict["values"]), ["left", "right"]
        )
        self.assertEqual(conflict["winner"]["source"], "b")
        self.assertEqual(
            sorted(side["source"] for side in conflict["sides"]), ["a", "b"]
        )

    def test_report_and_discards_query_by_key(self):
        self._concurrent_pair()
        merge(
            [
                {
                    "id": 40,
                    "v": "stale",
                    "version": {"a": 0},
                    "updated_at": 0,
                    "source": "a",
                }
            ]
        )
        self.assertEqual(len(report(key=40)), 1)
        self.assertEqual(report(key=999), [])
        self.assertEqual([row["reason"] for row in discards(key=40)], ["superseded_by_newer"])
        self.assertEqual(discards(key=999), [])

    def test_lookup_returns_row_conflicts_and_batches(self):
        self._concurrent_pair()
        entry = lookup(40)
        self.assertEqual(entry["row"]["v"], "right")
        self.assertEqual(len(entry["conflicts"]), 1)
        self.assertEqual(entry["batches"], [1, 2])
        self.assertEqual(entry["discards"], [])

    def test_conflict_sides_are_copies(self):
        result = self._concurrent_pair()
        result["conflicts"][0]["values"][0]["v"] = "mutated"
        self.assertEqual(sorted(row["v"] for row in report(key=40)[0]["values"]), ["left", "right"])


class AuditTest(unittest.TestCase):
    def setUp(self):
        reset(include_priority=True)

    def test_batches_are_archived_in_order(self):
        merge([{"id": 50, "v": 1, "version": {"a": 1}, "source": "a"}])
        merge([{"id": 50, "v": 2, "version": {"a": 2}, "source": "a"}])
        batches = audit()
        self.assertEqual([entry["batch"] for entry in batches], [1, 2])
        self.assertEqual(batches[1]["outcome"][50]["version"], {"a": 2})
        self.assertEqual(len(audit(batch=1)), 1)
        self.assertEqual(audit(batch=99), [])

    def test_conflicts_and_discards_are_batch_tagged(self):
        merge([{"id": 51, "v": "a", "version": {"a": 1}, "source": "a"}])
        merge([{"id": 51, "v": "b", "version": {"b": 1}, "source": "b"}], batch="nightly")
        merge([{"id": 51, "v": "old", "version": {"a": 1}, "source": "a"}])
        self.assertEqual(report(batch="nightly")[0]["key"], 51)
        self.assertEqual(report(batch=1), [])
        self.assertEqual(len(audit(batch="nightly")), 1)
        self.assertEqual([entry["batch"] for entry in audit()], [1, "nightly", 3])
        self.assertEqual(report(key=51)[0]["batch"], "nightly")

    def test_report_does_not_leak_internal_state(self):
        merge([{"id": 52, "v": "a", "version": {"a": 1}, "source": "a"}])
        merge([{"id": 52, "v": "b", "version": {"b": 1}, "source": "b"}])
        report(key=52)[0]["winner"]["source"] = "mutated"
        self.assertNotEqual(report(key=52)[0]["winner"]["source"], "mutated")

    def test_reset_clears_audit(self):
        merge([{"id": 53, "v": 1, "version": {"a": 1}, "source": "a"}])
        reset(include_priority=True)
        self.assertEqual(report(), [])
        self.assertEqual(audit(), [])
        self.assertEqual(merge([{"id": 53, "v": 2}])["batch"], 1)


if __name__ == "__main__":
    unittest.main()
