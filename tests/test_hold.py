import unittest

from crawlhold import hold, release


class HoldTest(unittest.TestCase):
    def test_hold_exits_immediately(self):
        result = hold("task-a")
        self.assertEqual(result["phase"], "exited")
        self.assertFalse(result["released"])

    def test_release_does_not_resume(self):
        self.assertFalse(release("task-a")["resumed"])

    def test_task_name_echoed(self):
        self.assertEqual(hold("task-b")["task"], "task-b")
