import importlib
import unittest

from taskhandoff import handoff, running

module = importlib.import_module("taskhandoff.handoff")


class HandoffTest(unittest.TestCase):
    def setUp(self):
        module._hosts.clear()

    def test_handoff_changes_host(self):
        handoff("t1", "m1", "m3")
        self.assertEqual(running("t1"), "m3")

    def test_no_checkpoint(self):
        result = handoff("t1", "m1", "m3")
        self.assertNotIn("checkpoint", result)

    def test_repeat_handoff_starts_again(self):
        handoff("t1", "m1", "m3")
        self.assertEqual(handoff("t1", "m3", "m1")["stage"], "started")
