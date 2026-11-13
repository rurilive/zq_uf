import unittest

from readclean import extract, extract_title

SAMPLE = """<html><head><title>示例文章</title></head>
<body><nav>首页 关于</nav>
<p>第一段内容。</p>
<p>第二段内容。</p>
<footer>版权</footer></body></html>
"""


class ReadcleanTest(unittest.TestCase):
    def test_title(self):
        self.assertEqual(extract_title(SAMPLE), "示例文章")

    def test_paragraphs_present(self):
        result = extract(SAMPLE)
        self.assertIn("第一段内容。", result["paragraphs"])

    def test_tags_removed(self):
        result = extract(SAMPLE)
        self.assertFalse(any("<" in part for part in result["paragraphs"]))
