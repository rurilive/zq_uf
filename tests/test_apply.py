import unittest

from cdcordering import apply, reset, snapshot


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
