import unittest

from pagecore import blocks, extract


class ExtractTest(unittest.TestCase):
    def test_text_strips_tags(self):
        self.assertEqual(extract("<p>hello</p>")["text"], "hello")

    def test_navigation_kept(self):
        html = "<nav>menu</nav><p>body</p>"
        self.assertIn("menu", extract(html)["text"])

    def test_single_block(self):
        self.assertEqual(len(blocks("<p>a</p><p>b</p>")), 1)

    def test_confidence_is_full(self):
        self.assertEqual(extract("<p>a</p>")["confidence"], 1.0)
