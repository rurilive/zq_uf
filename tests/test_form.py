import unittest

from formstep import begin, step


class FormTest(unittest.TestCase):
    def test_begin_token(self):
        self.assertEqual(begin("f1")["token"], "f1-0")

    def test_token_changes_each_step(self):
        begin("f2")
        self.assertNotEqual(step("f2", {})["token"], step("f2", {})["token"])

    def test_state_echoed(self):
        begin("f3")
        self.assertEqual(step("f3", {"a": 1})["state"], {"a": 1})

    def test_never_replayed(self):
        begin("f4")
        self.assertFalse(step("f4", {})["replayed"])
