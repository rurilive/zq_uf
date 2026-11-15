import unittest

from crawlchain import span, start


class ChainTest(unittest.TestCase):
    def test_start_returns_id(self):
        self.assertTrue(start("task-a")["trace"].startswith("t-"))

    def test_span_has_no_trace(self):
        self.assertIsNone(span("task-a", "fetch")["trace"])

    def test_span_elapsed_zero(self):
        self.assertEqual(span("task-a", "parse")["elapsed"], 0.0)
