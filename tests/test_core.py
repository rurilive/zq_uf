import unittest

from reconcheck import compare


class ReconcheckTest(unittest.TestCase):
    def test_left_only(self):
        result = compare([{"id": 1}, {"id": 2}], [{"id": 1}])
        self.assertEqual(result["left_only"], [2])

    def test_right_only(self):
        result = compare([{"id": 1}], [{"id": 1}, {"id": 3}])
        self.assertEqual(result["right_only"], [3])

    def test_counts(self):
        result = compare([{"id": 1}], [{"id": 1}])
        self.assertEqual((result["rows_left"], result["rows_right"]), (1, 1))
