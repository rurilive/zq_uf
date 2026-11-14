import importlib
import unittest

from qualityscore import score, trend

module = importlib.import_module("qualityscore.score")


class ScoreTest(unittest.TestCase):
    def setUp(self):
        module._history.clear()

    def test_all_pass(self):
        self.assertEqual(score({"rules": [{"failed": False}]})["score"], 1.0)

    def test_half_fail(self):
        self.assertEqual(score({"rules": [{"failed": True}, {"failed": False}]})["score"], 0.5)

    def test_trend_appends(self):
        score({"dataset": "d", "rules": [{"failed": False}]})
        self.assertEqual(trend("d"), [1.0])
