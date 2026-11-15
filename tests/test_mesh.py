import unittest

from proxymesh import pick, report


class MeshTest(unittest.TestCase):
    def test_pick_returns_known_proxy(self):
        self.assertIn(pick("a")["proxy"], ["proxy-a", "proxy-b"])

    def test_pick_has_no_health(self):
        self.assertIsNone(pick("b")["health"])

    def test_report_does_not_switch(self):
        self.assertFalse(report("proxy-a", False, 2.0)["switched"])
