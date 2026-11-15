import unittest

from partmove2 import move, rollback


class MoveTest(unittest.TestCase):
    def test_move_reports_done(self):
        self.assertEqual(move("p1", "t1")["phase"], "done")

    def test_no_position(self):
        self.assertIsNone(move("p2", "t2")["position"])

    def test_rollback_empty(self):
        self.assertFalse(rollback()["rolled_back"])
