import unittest

from sessionpin import bind, rebind
from sessionpin import pin


class PinTest(unittest.TestCase):
    def setUp(self):
        pin._bindings.clear()

    def test_bind_returns_site(self):
        self.assertEqual(bind("s1", "shop")["site"], "shop")

    def test_bind_overwrites(self):
        bind("s1", "shop"); bind("s1", "news")
        self.assertEqual(bind("s1", "shop")["site"], "shop")

    def test_rebind_changes_proxy(self):
        bind("s1", "shop")
        self.assertEqual(rebind("s1", "failed")["proxy"], "10.0.0.2:80")
