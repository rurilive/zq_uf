import unittest

from changedetect import diff, should_ingest


class DiffTest(unittest.TestCase):
    def test_identical(self):
        self.assertFalse(diff("<p>a</p>", "<p>a</p>")["changed"])

    def test_ad_noise_triggers(self):
        self.assertTrue(should_ingest(diff("<p>a</p>", "<p>a</p><div>ad</div>")))

    def test_no_levels(self):
        self.assertNotIn("level", diff("a", "b"))
