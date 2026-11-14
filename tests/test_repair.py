import unittest

from lineagerepair import audit, confirm, infer, register, registry, reset, rollback


class RepairTest(unittest.TestCase):
    def test_infer_returns_empty(self):
        self.assertEqual(infer([{"job": "j1", "read": "a", "write": "b"}]), [])

    def test_confirm_returns_edge(self):
        self.assertEqual(confirm(("a", "b"), "alice")["edge"], ("a", "b"))

    def test_no_confidence(self):
        self.assertNotIn("confidence", infer([]))


class InferenceTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_infer_edge_from_same_task_logs(self):
        logs = [
            {"job": "j1", "batch": "b1", "read": "a", "write": "b"},
            {"job": "j1", "batch": "b1", "read": "a", "write": "b"},
        ]
        result = infer(logs)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["edge"], ("a", "b"))
        self.assertEqual(result[0]["evidence"][0]["job"], "j1")
        self.assertEqual(result[0]["evidence"][0]["batch"], "b1")

    def test_infer_read_write_list_fields(self):
        logs = [
            {"job": "j1", "batch": "b1", "read": ["a", "c"], "write": ["b"]},
            {"job": "j1", "batch": "b1", "read": ["a", "c"], "write": ["b"]},
        ]
        edges = {item["edge"] for item in infer(logs)}
        self.assertEqual(edges, {("a", "b"), ("c", "b")})

    def test_single_record_is_not_enough_evidence(self):
        logs = [{"job": "j1", "batch": "b1", "read": "a", "write": "b"}]
        self.assertEqual(infer(logs), [])

    def test_confidence_levels_cross_batch_vs_same_batch(self):
        cross_batch = infer(
            [
                {"job": "j1", "batch": "b1", "read": "a", "write": "b"},
                {"job": "j1", "batch": "b2", "read": "a", "write": "b"},
            ]
        )
        self.assertEqual(cross_batch[0]["confidence"], 0.9)
        self.assertEqual(cross_batch[0]["level"], "high")

        same_batch = infer(
            [
                {"job": "j2", "batch": "b1", "read": "c", "write": "d"},
                {"job": "j2", "batch": "b1", "read": "c", "write": "d"},
            ]
        )
        self.assertEqual(same_batch[0]["confidence"], 0.7)
        self.assertEqual(same_batch[0]["level"], "medium")

    def test_existing_registration_takes_priority(self):
        register(("a", "b"), "bob")
        result = infer(
            [
                {"job": "j1", "batch": "b1", "read": "a", "write": "b"},
                {"job": "j1", "batch": "b1", "read": "a", "write": "b"},
            ]
        )
        self.assertEqual(result, [])
        self.assertIn(("a", "b"), registry())

    def test_conflict_keeps_both_sides_and_marks_pending(self):
        register(("a", "x"), "bob")
        result = infer(
            [
                {"job": "j1", "batch": "b1", "read": "a", "write": "b"},
                {"job": "j1", "batch": "b2", "read": "a", "write": "b"},
            ]
        )
        self.assertEqual(len(result), 1)
        inferred = result[0]
        self.assertEqual(inferred["status"], "conflict")
        self.assertEqual(inferred["conflicts_with"], [("a", "x")])
        self.assertIn(("a", "x"), registry())

    def test_confirm_writes_back_with_audit_and_evidence(self):
        logs = [
            {"job": "j1", "batch": "b1", "read": "a", "write": "b"},
            {"job": "j1", "batch": "b2", "read": "a", "write": "b"},
        ]
        inferred = infer(logs)[0]
        reply = confirm(inferred["edge"], "alice")
        self.assertTrue(reply["confirmed"])
        stored = registry()[inferred["edge"]]
        self.assertEqual(stored["operator"], "alice")
        self.assertEqual(stored["source"], "confirmed")
        self.assertEqual(len(stored["evidence"]), 2)
        entries = audit()
        self.assertEqual(entries[-1]["action"], "confirm")
        self.assertEqual(entries[-1]["operator"], "alice")

    def test_rollback_confirmed_edge(self):
        logs = [
            {"job": "j1", "batch": "b1", "read": "a", "write": "b"},
            {"job": "j1", "batch": "b1", "read": "a", "write": "b"},
        ]
        edge = infer(logs)[0]["edge"]
        confirm(edge, "alice")
        self.assertIn(edge, registry())
        reply = rollback(edge, "alice")
        self.assertTrue(reply["rolled_back"])
        self.assertNotIn(edge, registry())
        self.assertEqual(audit()[-1]["action"], "rollback")

    def test_rollback_restores_previous_registration(self):
        register(("a", "b"), "bob")
        confirm(("a", "b"), "alice")
        rollback(("a", "b"), "alice")
        self.assertEqual(registry()[("a", "b")]["operator"], "bob")

    def test_rollback_without_action_is_reported(self):
        reply = rollback(("a", "b"), "alice")
        self.assertFalse(reply["rolled_back"])
        self.assertEqual(reply["reason"], "nothing_to_rollback")

    def test_confirm_conflict_keeps_both_sides(self):
        register(("a", "x"), "bob")
        infer(
            [
                {"job": "j1", "batch": "b1", "read": "a", "write": "b"},
                {"job": "j1", "batch": "b1", "read": "a", "write": "b"},
            ]
        )
        confirm(("a", "b"), "alice")
        self.assertIn(("a", "x"), registry())
        self.assertIn(("a", "b"), registry())
        self.assertEqual(registry()[("a", "b")]["conflicts_with"], [("a", "x")])


if __name__ == "__main__":
    unittest.main()
