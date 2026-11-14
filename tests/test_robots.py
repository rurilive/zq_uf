import unittest

from robotsdiff import affected, allowed, update
from robotsdiff import robots


class RobotsTest(unittest.TestCase):
    def setUp(self):
        robots._rules.clear()

    def test_update_stores(self):
        self.assertEqual(update("shop", {"disallow": ["/p"]})["disallow"], ["/p"])

    def test_allowed_uses_latest(self):
        update("shop", {"disallow": ["/p"]})
        self.assertFalse(allowed("shop", "/p/1"))

    def test_affected_empty(self):
        self.assertEqual(affected("shop"), [])
