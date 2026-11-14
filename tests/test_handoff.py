import importlib
import unittest

from taskhandoff import handoff, running

module = importlib.import_module("taskhandoff.handoff")


class HandoffTest(unittest.TestCase):
    def setUp(self):
        module._hosts.clear()
        module._states.clear()
        module._faults.clear()

    def test_handoff_changes_host(self):
        handoff("t1", "m1", "m3")
        self.assertEqual(running("t1"), "m3")

    def test_no_checkpoint(self):
        result = handoff("t1", "m1", "m3")
        self.assertNotIn("checkpoint", result)

    def test_repeat_handoff_starts_again(self):
        handoff("t1", "m1", "m3")
        self.assertEqual(handoff("t1", "m3", "m1")["stage"], "started")

    def _seed(self, task_id, host, written, checkpoint, source):
        module._hosts[task_id] = host
        state = module._state(task_id)
        state.written = written
        state.checkpoint = checkpoint
        state.source = source
        for seq in range(written):
            key = module._idem_key(task_id, seq)
            state.sink[key] = module._payload(task_id, seq)
        return state

    def test_dual_write_dedup(self):
        state = self._seed("t2", "m1", written=5, checkpoint=3, source=5)
        result = handoff("t2", "m1", "m3")
        self.assertEqual(result["idempotent_hits"], 2)
        self.assertEqual(len(state.sink), 5)

    def test_stop_old_after_catch_up(self):
        state = self._seed("t3", "m1", written=5, checkpoint=5, source=8)
        result = handoff("t3", "m1", "m3")
        self.assertEqual(result["stage"], "started")
        self.assertEqual(
            result["phases"],
            ["checkpoint", "dual_write", "catch_up", "stop_old"],
        )
        self.assertEqual(running("t3"), "m3")
        self.assertEqual(state.written, 8)
        self.assertFalse(state.read_only)
        self.assertEqual(len(state.sink), 8)

    def test_failure_rolls_back(self):
        state = self._seed("t4", "m1", written=5, checkpoint=3, source=8)
        module._faults["t4"] = "catch_up"
        result = handoff("t4", "m1", "m3")
        self.assertEqual(result["stage"], "rolled_back")
        self.assertTrue(result["rollback_reason"])
        self.assertEqual(running("t4"), "m1")
        self.assertFalse(state.read_only)
        self.assertEqual(len(state.sink), 8)

        del module._faults["t4"]
        retry = handoff("t4", "m1", "m3")
        self.assertEqual(retry["stage"], "started")
        self.assertEqual(retry["idempotent_hits"], 5)
        self.assertEqual(running("t4"), "m3")

    def test_repeat_handoff_is_duplicate(self):
        handoff("t5", "m1", "m3")
        again = handoff("t5", "m1", "m3")
        self.assertEqual(again["stage"], "duplicate")
        self.assertEqual(again["idempotent_hits"], 0)
        self.assertEqual(running("t5"), "m3")
