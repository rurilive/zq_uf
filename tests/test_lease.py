import importlib
import unittest

from siteleasectl import lease, release, usage

module = importlib.import_module("siteleasectl.lease")


class LeaseTest(unittest.TestCase):
    def setUp(self):
        module._used = 0

    def test_lease(self):
        self.assertEqual(lease("shop")["site"], "shop")

    def test_global_limit(self):
        lease("shop")
        lease("shop")
        lease("news")
        with self.assertRaises(RuntimeError):
            lease("blog")

    def test_no_expiry(self):
        lease("shop")
        self.assertEqual(usage()["used"], 1)
