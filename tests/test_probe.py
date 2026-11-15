import unittest

from crawlprobe2 import probe, verdict


class ProbeTest(unittest.TestCase):
    def test_probe_returns_one_row_per_site(self):
        self.assertEqual(len(probe(["a", "b"])["results"]), 2)

    def test_probe_not_cached(self):
        self.assertEqual(probe(["a"])["cache_hits"], 0)

    def test_verdict_defaults_to_ok(self):
        self.assertEqual(verdict("z")["verdict"], "ok")
