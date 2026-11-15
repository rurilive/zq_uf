import unittest

from sitequarantine import probe, quarantine


class QuarantineTest(unittest.TestCase):
    def test_quarantine_keeps_reason(self):
        self.assertEqual(quarantine("a", "blocked")["reason"], "blocked")

    def test_quarantine_has_no_samples(self):
        self.assertEqual(quarantine("b", "timeout")["samples"], [])

    def test_probe_always_open(self):
        quarantine("c", "blocked")
        self.assertEqual(probe("c")["verdict"], "open")
