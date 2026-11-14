import unittest

from crawlprobe import probe, verdict


class ProbeTest(unittest.TestCase):
    def test_probe_ok(self):
        self.assertTrue(probe("shop")["ok"])

    def test_probe_echoes_site(self):
        self.assertEqual(probe("shop")["site"], "shop")

    def test_verdict_ok(self):
        self.assertEqual(verdict("shop"), "ok")
