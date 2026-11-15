import unittest

from pagecore import blocks, configure, diff, extract

LONG = "这是正文段落，用来验证按块抽取的效果，字数要足够多才能拿到较高的密度分。" * 3


class ExtractTest(unittest.TestCase):
    def test_navigation_removed(self):
        html = "<nav>首页 推荐 热榜</nav><article><p>%s</p></article>" % LONG
        r = extract(html)
        self.assertIn("正文段落", r["text"])
        self.assertNotIn("热榜", r["text"])
        self.assertTrue(any(b["tag"] == "nav" for b in r["noise"]))

    def test_link_block_removed(self):
        links = "<div>" + "".join(
            '<a href="/%d">链接%d</a>' % (i, i) for i in range(5)) + "</div>"
        r = extract(links + "<p>%s</p>" % LONG)
        self.assertNotIn("链接1", r["text"])
        self.assertEqual(r["noise"][0]["reason"], "link-density")

    def test_comment_block_removed_by_pattern(self):
        html = '<p>%s</p><div class="comment-list">网友回复一堆</div>' % LONG
        r = extract(html)
        self.assertNotIn("网友回复", r["text"])
        self.assertEqual(r["noise"][0]["reason"], "noise-pattern")

    def test_short_article_kept_with_low_confidence(self):
        r = extract("<article><p>短正文一段。</p><p>短正文二段。</p></article>")
        self.assertIn("短正文一段", r["text"])
        self.assertLess(r["confidence"], 0.5)

    def test_long_article_high_confidence(self):
        body = "".join("<p>%s</p>" % LONG for _ in range(3))
        r = extract("<article>%s</article>" % body)
        self.assertGreater(r["confidence"], 0.5)

    def test_blocks_marks_kept_and_reason(self):
        bs = blocks("<nav>菜单</nav><p>%s</p>" % LONG)
        self.assertEqual(len(bs), 2)
        self.assertFalse(bs[0]["kept"])
        self.assertTrue(bs[1]["kept"])
        self.assertEqual(bs[1]["reason"], "content")

    def test_site_rules_override(self):
        html = '<div class="recommend">%s</div>' % LONG
        self.assertEqual(extract(html)["text"], "")
        configure("rec.example.com", noise_patterns=())
        self.assertIn("正文段落", extract(html, site="rec.example.com")["text"])

    def test_configure_rejects_unknown_key(self):
        with self.assertRaises(ValueError):
            configure("bad.example.com", no_such_rule=1)

    def test_fallback_keeps_previous_by_url(self):
        url = "http://a.example.com/1"
        good = extract("<p>%s</p>" % LONG, url=url)
        bad = extract("<div></div>", url=url)
        self.assertTrue(bad["fallback"])
        self.assertEqual(bad["text"], good["text"])

    def test_fallback_with_explicit_previous(self):
        good = extract("<p>%s</p>" % LONG)
        bad = extract("", previous=good)
        self.assertTrue(bad["fallback"])
        self.assertEqual(bad["text"], good["text"])

    def test_no_previous_returns_empty_with_error(self):
        r = extract("")
        self.assertEqual(r["text"], "")
        self.assertFalse(r["fallback"])
        self.assertIn("error", r)

    def test_diff_shows_changes(self):
        d = diff({"text": "旧正文"}, {"text": "新正文"})
        self.assertTrue(any(l.startswith("-") and "旧正文" in l for l in d))
        self.assertTrue(any(l.startswith("+") and "新正文" in l for l in d))


if __name__ == "__main__":
    unittest.main()
