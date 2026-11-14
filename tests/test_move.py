import importlib
import unittest

from partitionmove import move, rollback

module = importlib.import_module("partitionmove.move")


class MoveTest(unittest.TestCase):
    def setUp(self):
        module._moved.clear()

    def test_move_records(self):
        self.assertEqual(move("p1", "cold")["target"], "cold")

    def test_move_counts(self):
        self.assertEqual(move("p1", "cold")["moved"], 1)

    def test_rollback_unsupported(self):
        with self.assertRaises(NotImplementedError):
            rollback()
