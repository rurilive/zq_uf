import unittest

from contenthash import fingerprint, similar


class DigestTest(unittest.TestCase):
    def test_same_html_same_hash(self):
        self.assertEqual(fingerprint("<p>a</p>"), fingerprint("<p>a</p>"))

    def test_whitespace_changes_hash(self):
        self.assertNotEqual(fingerprint("<p>a</p>"), fingerprint("<p> a </p>"))

    def test_navigation_changes_hash(self):
        left = "<nav>x</nav><p>body</p>"
        right = "<nav>y</nav><p>body</p>"
        self.assertNotEqual(fingerprint(left), fingerprint(right))

    def test_similar_only_exact(self):
        self.assertFalse(similar("<p>a</p>", "<p>a</p> "))
