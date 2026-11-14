import unittest

from formstate import start, step


class FormTest(unittest.TestCase):
    def test_step_increments(self):
        state = start("login")
        step(state, {"user": "a"})
        self.assertEqual(state["step"], 1)

    def test_repeat_submits_again(self):
        state = start("login")
        step(state, {"user": "a"})
        step(state, {"user": "a"})
        self.assertEqual(state["step"], 2)

    def test_no_idempotency_key(self):
        self.assertNotIn("idem_key", start("login"))
