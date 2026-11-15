import unittest

from pagefinger import fingerprint, similar


class FingerTest(unittest.TestCase):
    def test_same_page_same_hash(self):
        self.assertEqual(fingerprint("<p>a</p>")["hash"], fingerprint("<p>a</p>")["hash"])

    def test_whitespace_changes_hash(self):
        self.assertNotEqual(fingerprint("<p>a</p>")["hash"], fingerprint("<p> a </p>")["hash"])

    def test_different_pages_not_similar(self):
        self.assertFalse(similar("<p>a</p>", "<p>b</p>")["similar"])
