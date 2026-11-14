import unittest

from cdcordering import EventError, apply, conflicts, reset, snapshot


class ApplyTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_later_event_wins(self):
        apply([{"key": "k", "row": {"v": 2}}, {"key": "k", "row": {"v": 1}}])
        self.assertEqual(snapshot()["k"]["v"], 1)

    def test_delete_can_be_resurrected(self):
        apply([{"key": "k", "row": {"v": 2}}, {"key": "k", "row": {"v": 1, "deleted": False}}])
        self.assertFalse(snapshot()["k"]["deleted"])

    def test_no_conflicts(self):
        apply([{"key": "k", "row": {"v": 1}}])
        self.assertNotIn("conflicts", snapshot())

    def test_out_of_order_converges_by_version(self):
        result = apply(
            [
                {"key": "k", "row": {"v": 2}, "version": {"s": 2}},
                {"key": "k", "row": {"v": 1}, "version": {"s": 1}},
            ]
        )
        self.assertEqual(snapshot()["k"]["v"], 2)
        self.assertEqual(result["discarded"][0]["reason"], "stale")

    def test_tombstone_blocks_resurrection(self):
        apply(
            [
                {"key": "k", "row": {"v": 1, "deleted": True}, "version": {"s": 2}},
                {"key": "k", "row": {"v": 9}, "version": {"s": 1}},
            ]
        )
        self.assertTrue(snapshot()["k"]["deleted"])
        self.assertEqual(snapshot()["k"]["version"], {"s": 2})

    def test_duplicate_delivery_idempotent(self):
        event = {"key": "k", "row": {"v": 1}, "version": {"s": 1}}
        result = apply([event, dict(event)])
        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["discarded"][0]["reason"], "duplicate")
        self.assertEqual(snapshot()["k"]["v"], 1)

    def test_conflict_keeps_both_sides(self):
        result = apply(
            [
                {"key": "k", "row": {"v": 1}, "version": {"a": 1}, "source": "a"},
                {"key": "k", "row": {"v": 2}, "version": {"b": 1}, "source": "b"},
            ]
        )
        self.assertEqual(snapshot()["k"]["v"], 1)
        self.assertEqual(len(result["conflicts"]), 1)
        entry = result["conflicts"][0]
        self.assertEqual(entry["existing"]["row"]["v"], 1)
        self.assertEqual(entry["existing"]["source"], "a")
        self.assertEqual(entry["incoming"]["row"]["v"], 2)
        self.assertEqual(entry["incoming"]["source"], "b")
        self.assertEqual(conflicts(), result["conflicts"])

    def test_batch_atomic_on_invalid_event(self):
        apply([{"key": "k", "row": {"v": 1}, "version": {"s": 1}}])
        with self.assertRaises(EventError):
            apply(
                [
                    {"key": "x", "row": {"v": 1}, "version": {"s": 1}},
                    {"key": "y", "row": "not-a-dict"},
                ]
            )
        self.assertNotIn("x", snapshot())
        self.assertEqual(snapshot()["k"]["v"], 1)

    def test_dry_run_previews_without_mutating(self):
        apply([{"key": "k", "row": {"v": 1}, "version": {"s": 1}}])
        result = apply(
            [{"key": "k", "row": {"v": 2}, "version": {"s": 2}}], dry_run=True
        )
        self.assertEqual([c["key"] for c in result["changes"]], ["k"])
        self.assertEqual(result["changes"][0]["after"]["v"], 2)
        self.assertEqual(snapshot()["k"]["v"], 1)
