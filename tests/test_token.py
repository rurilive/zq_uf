import unittest

from csrftoken import get, invalidate


class TokenTest(unittest.TestCase):
    def test_get_returns_token(self):
        self.assertTrue(get("p1", 1)["token"])

    def test_step_echoed(self):
        self.assertEqual(get("p1", 2)["step"], 2)

    def test_invalidate(self):
        get("p1", 1)
        self.assertTrue(invalidate("p1"))
