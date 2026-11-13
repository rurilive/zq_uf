import unittest

from loginflow import run


class LoginflowTest(unittest.TestCase):
    def test_steps_share_context(self):
        steps = [lambda ctx: {"token": "t"}, lambda ctx: {"done": ctx["token"] + "!"}]
        self.assertEqual(run("acme", steps), {"account": "acme", "token": "t", "done": "t!"})

    def test_empty_steps(self):
        self.assertEqual(run("acme", []), {"account": "acme"})
