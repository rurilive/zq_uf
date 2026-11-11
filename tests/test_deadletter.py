import unittest

from crawlflow import (
    NetworkError,
    ParseError,
    PermanentError,
    Scheduler,
    classify_error,
    classify_http_status,
)


class DeadLetterTest(unittest.TestCase):
    def setUp(self):
        self.s = Scheduler(backoff_base=0.01)

    def test_permanent_error_goes_straight_to_dead_letter(self):
        self.s.add("https://example.com/gone")
        task = self.s.next()
        self.s.failure(task.url, PermanentError("404"))
        self.assertIsNone(self.s.next())
        dead = self.s.dead_letters()
        self.assertEqual(len(dead), 1)
        self.assertEqual(dead[0][0], "https://example.com/gone")
        self.assertEqual(dead[0][2], 1)  # 不重试，一次即落死信
        self.assertEqual(self.s.stats().get("dead"), 1)

    def test_parse_error_sampled_and_skipped(self):
        self.s.add("https://example.com/broken")
        task = self.s.next()
        self.s.failure(
            task.url,
            ParseError("missing <article>", sample="<html>empty</html>"),
        )
        self.assertIsNone(self.s.next())  # 跳过，不重试
        samples = self.s.parse_samples()
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0][0], "https://example.com/broken")
        self.assertIn("missing <article>", samples[0][1])
        self.assertEqual(samples[0][2], "<html>empty</html>")
        self.assertEqual(self.s.stats().get("skipped"), 1)

    def test_parse_error_without_sample_object(self):
        self.s.add("https://example.com/broken")
        task = self.s.next()
        self.s.failure(task.url, ParseError("bad html"), sample="raw...")
        self.assertEqual(self.s.parse_samples()[0][2], "raw...")

    def test_network_retries_then_dead_letter(self):
        s = Scheduler(backoff_base=0.0, max_attempts=3)
        s.add("https://example.com/flaky")
        for _ in range(3):
            task = s.next()
            self.assertIsNotNone(task)
            s.failure(task.url, NetworkError("connection reset"))
        self.assertIsNone(s.next())
        self.assertEqual(len(s.dead_letters()), 1)

    def test_dead_letter_not_resurrected_by_add(self):
        self.s.add("https://example.com/gone")
        task = self.s.next()
        self.s.failure(task.url, PermanentError("410"))
        self.assertFalse(self.s.add("https://example.com/gone"))
        self.assertIsNone(self.s.next())

    def test_classify_helpers(self):
        self.assertEqual(classify_http_status(404), "permanent")
        self.assertEqual(classify_http_status(429), "network")
        self.assertEqual(classify_http_status(503), "network")
        self.assertEqual(classify_error(TimeoutError("t")), "network")
        self.assertEqual(classify_error(ValueError("v")), "network")
        self.assertEqual(classify_error(None), "unknown")


if __name__ == "__main__":
    unittest.main()
