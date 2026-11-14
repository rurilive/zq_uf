import unittest

from taskfair import next_task, reset, submit


class SchedTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_fifo_across_queues(self):
        submit("big", {"id": 1})
        submit("small", {"id": 2})
        self.assertEqual(next_task()["id"], 1)

    def test_no_weights(self):
        self.assertNotIn("weights", submit("big", {"id": 1}) if isinstance(submit("big", {"id": 1}), dict) else {})

    def test_no_preemption(self):
        submit("big", {"id": 1})
        self.assertNotIn("preempted", next_task())
