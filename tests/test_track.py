import unittest

from pagetrack import changes, save


class TrackTest(unittest.TestCase):
    def test_save_has_no_version(self):
        self.assertIsNone(save("https://a/1", "<p>a</p>")["version"])

    def test_save_has_no_fingerprint(self):
        self.assertIsNone(save("https://a/2", "<p>b</p>")["fingerprint"])

    def test_changes_empty(self):
        save("https://a/1", "<p>a</p>")
        self.assertEqual(changes(0)["changes"], [])
