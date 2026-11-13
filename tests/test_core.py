import unittest

from retryall import call


class RetryallTest(unittest.TestCase):
    def test_success_first(self):
        self.assertEqual(call(lambda: 1), 1)

    def test_retries_then_success(self):
        state = {"n": 0}

        def flaky():
            state["n"] += 1
            if state["n"] < 3:
                raise ValueError("boom")
            return "ok"

        self.assertEqual(call(flaky), "ok")

    def test_raises_after_attempts(self):
        with self.assertRaises(ValueError):
            call(lambda: (_ for _ in ()).throw(ValueError("x")), {"attempts": 2})
