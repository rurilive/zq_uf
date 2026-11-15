import unittest

from seedscore import filter, score


class ScoreTest(unittest.TestCase):
    def test_score_is_full(self):
        self.assertEqual(score({"url": "https://a"})["score"], 1.0)

    def test_filter_keeps_all(self):
        self.assertEqual(len(filter([{"url": "https://a"}, {"url": "https://b"}])["kept"]), 2)

    def test_no_drop_reason(self):
        self.assertEqual(filter([])["reasons"], [])
