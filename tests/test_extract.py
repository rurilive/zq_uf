import unittest

from pagecore import (
    ExtractionError,
    Extractor,
    blocks,
    diff_results,
    extract,
)

ARTICLE = """
<html><body>
<nav class="site-nav"><a>首页</a><a>专栏</a><a>关于我们</a></nav>
<article>
<p>第一段正文，讨论页面正文抽取的密度特征，长度足够参与打分。</p>
<p>第二段正文，继续展开说明链接比例如何帮助区分正文和导航。</p>
<p>第三段正文，收尾总结，同样保持足够的字数和标点密度。</p>
</article>
<div class="comment-list"><p>网友：说得对</p><p>网友：顶一下</p></div>
<aside class="recommend"><a>推荐文章一</a><a>推荐文章二</a></aside>
<footer>版权所有 2026</footer>
</body></html>
"""

SHORT_ARTICLE = "<article><p>短正文第一段，只有两句话。</p><p>短正文第二段，收尾。</p></article>"

LIST_PAGE = "<ul>" + "".join(
    "<li><a>链接标题%d</a></li>" % i for i in range(20)
) + "</ul>"


class ExtractTest(unittest.TestCase):
    def test_body_kept_noise_dropped(self):
        result = extract(ARTICLE)
        self.assertIn("第一段正文", result["text"])
        self.assertIn("第三段正文", result["text"])
        for noise in ("首页", "网友", "推荐文章", "版权所有"):
            self.assertNotIn(noise, result["text"])

    def test_noise_blocks_marked(self):
        result = extract(ARTICLE)
        self.assertTrue(result["noise"])
        self.assertTrue(result["kept"])
        for b in result["blocks"]:
            if b["index"] in result["noise"]:
                self.assertFalse(b["kept"])
                self.assertTrue(b["reason"].startswith("noise"))
            if b["index"] in result["kept"]:
                self.assertEqual(b["reason"], "content")

    def test_word_count_not_inflated(self):
        result = extract(ARTICLE)
        body_chars = sum(
            len(t) for t in ("第一段正文，讨论页面正文抽取的密度特征，长度足够参与打分。",
                             "第二段正文，继续展开说明链接比例如何帮助区分正文和导航。",
                             "第三段正文，收尾总结，同样保持足够的字数和标点密度。")
        )
        self.assertEqual(result["chars"], body_chars)

    def test_short_article_kept_with_low_confidence(self):
        result = extract(SHORT_ARTICLE)
        self.assertIn("短正文第一段", result["text"])
        self.assertIn("短正文第二段", result["text"])
        self.assertTrue(result["short"])
        self.assertGreater(result["confidence"], 0.0)
        self.assertLessEqual(result["confidence"], 0.4)

    def test_list_page_yields_no_body(self):
        result = extract(LIST_PAGE)
        self.assertEqual(result["text"], "")
        self.assertEqual(result["confidence"], 0.0)
        self.assertTrue(all(
            b["reason"] == "link-heavy" for b in result["blocks"]
        ))

    def test_blocks_report_scores(self):
        for b in blocks(ARTICLE):
            self.assertIn("score", b)
            self.assertIn("link_ratio", b)
            self.assertIn("kept", b)
            self.assertIn("reason", b)

    def test_site_config_override(self):
        html = ('<div class="comment-list"><p>'
                "这段其实是正文，长度要超过十个字才能参与打分。"
                "</p></div>")
        self.assertEqual(extract(html)["text"], "")
        result = extract(html, config={"noise_keywords": ()})
        self.assertIn("这段其实是正文", result["text"])

    def test_extractor_keeps_previous_on_failure(self):
        ex = Extractor()
        first = ex.extract(ARTICLE)
        self.assertFalse(first["stale"])
        for bad in ("", LIST_PAGE):
            stale = ex.extract(bad)
            self.assertTrue(stale["stale"])
            self.assertEqual(stale["text"], first["text"])

    def test_extractor_raises_without_previous(self):
        self.assertRaises(ExtractionError, Extractor().extract, "")

    def test_diff_results(self):
        new_html = ARTICLE.replace(
            "第三段正文，收尾总结，同样保持足够的字数和标点密度。",
            "第三段正文，改版后的新结尾，字数同样保持足够。",
        )
        diff = diff_results(extract(ARTICLE), extract(new_html))
        self.assertEqual(diff["unchanged"], 2)
        self.assertEqual(len(diff["added"]), 1)
        self.assertEqual(len(diff["removed"]), 1)
        self.assertIn("改版后的新结尾", diff["added"][0])


if __name__ == "__main__":
    unittest.main()
