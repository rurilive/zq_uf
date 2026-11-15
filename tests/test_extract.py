import unittest

from pagecore import blocks, extract

ARTICLE = (
    "<html><body>"
    "<nav><a>首页</a><a>推荐</a><a>登录</a></nav>"
    "<article>"
    "<p>本报讯，记者昨日从发布会现场了解到，新一代采集系统已经正式上线，"
    "覆盖了全国三十一个省市自治区的数据节点，日均处理页面超过两亿个。</p>"
    "<p>项目负责人介绍说，系统采用了全新的分布式架构，"
    "在保证稳定性的同时，将单页处理耗时压缩到了毫秒级，"
    "为下游的搜索和推荐业务提供了坚实的数据支撑。</p>"
    "</article>"
    "<aside><a>热门推荐：某产品发布会</a><a>猜你喜欢：行业动态</a></aside>"
    "<div class=\"comments\"><a>网友甲</a>：说得好！<a>网友乙</a>：顶一下</div>"
    "<footer>版权所有 违者必究</footer>"
    "</body></html>"
)

# 窄栏文章：每段一个 div，单段都不长
NARROW = (
    "<div class=\"col\"><p>清晨的山谷里雾气很重，能见度不足五十米，"
    "巡山员还是照常出发了。</p></div>"
    "<div class=\"col\"><p>他说这条路线走了二十年，哪里有暗沟、"
    "哪里有陡坡，闭着眼都数得出来。</p></div>"
    "<div class=\"col\"><p>傍晚回到管护站，他把当天的记录誊进台账，"
    "字迹工整，一笔不落。</p></div>"
)


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


class ScoringTest(unittest.TestCase):
    def test_link_heavy_block_scores_low(self):
        [nav] = blocks("<nav><a>首页</a><a>推荐</a><a>登录</a></nav>")
        self.assertLess(nav["score"], 1.0)
        self.assertGreater(nav["link_density"], 0.9)
        self.assertFalse(nav["is_content"])

    def test_dense_paragraph_scores_high(self):
        [article] = blocks("<article><p>" + "正文内容，有标点。" * 20 + "</p></article>")
        self.assertGreaterEqual(article["score"], 6.0)
        self.assertTrue(article["is_content"])

    def test_scores_exported(self):
        result = extract(ARTICLE)
        self.assertEqual(len(result["scores"]), len(blocks(ARTICLE)))


class NoiseTest(unittest.TestCase):
    def test_nav_aside_comments_filtered(self):
        result = extract(ARTICLE)
        self.assertIn("分布式架构", result["text"])
        self.assertNotIn("热门推荐", result["text"])
        self.assertNotIn("说得好", result["text"])
        self.assertFalse(result["low_confidence"])

    def test_noise_listed(self):
        result = extract(ARTICLE)
        joined = " ".join(result["noise"])
        self.assertIn("热门推荐", joined)
        self.assertIn("说得好", joined)

    def test_content_block_marked(self):
        marks = {b["tag"]: b["is_content"] for b in blocks(ARTICLE)}
        self.assertTrue(marks["article"])
        self.assertFalse(marks["nav"])
        self.assertFalse(marks["aside"])


class ShortContentTest(unittest.TestCase):
    def test_short_content_kept_with_low_confidence(self):
        html = "<nav>menu</nav><article><p>公告：今日停更。</p></article>"
        result = extract(html)
        self.assertIn("今日停更", result["text"])
        self.assertTrue(result["low_confidence"])

    def test_single_short_block_kept(self):
        result = extract("<p>短。</p>")
        self.assertIn("短。", result["text"])
        self.assertTrue(result["low_confidence"])


class SiteOverrideTest(unittest.TestCase):
    def test_default_rules_miss_narrow_column(self):
        result = extract(NARROW)
        self.assertTrue(result["low_confidence"])

    def test_site_rules_recover_narrow_column(self):
        result = extract(NARROW, site="narrow.example")
        self.assertIn("暗沟", result["text"])
        self.assertIn("台账", result["text"])
        self.assertFalse(result["low_confidence"])
        self.assertEqual(result["rule"]["name"], "narrow-v2")

    def test_rules_version_rollback(self):
        old = extract(NARROW, site="narrow.example", rules_version=0)
        self.assertEqual(old["rule"]["name"], "narrow-v1")
        self.assertTrue(old["low_confidence"])

    def test_config_override(self):
        result = extract(NARROW, config={"min_score": 2.5})
        self.assertIn("台账", result["text"])


class BoundaryTest(unittest.TestCase):
    def test_boundaries_point_at_content(self):
        result = extract(ARTICLE)
        b = result["boundaries"]
        self.assertEqual(b["start_block"], 1)
        self.assertGreater(b["end_block"], b["start_block"] - 1)
        self.assertGreater(b["end"], b["start"])

    def test_long_article_keeps_tail(self):
        html = ("<div class=\"content\">"
                "<div>" + "第一段，" * 30 + "。</div>"
                "<div>" + "中间段，" * 30 + "。</div>"
                "<div>尾段收束全文，交代了最终的结果和后续安排，至此结束。</div>"
                "</div><aside><a>相关阅读</a></aside>")
        result = extract(html)
        self.assertIn("尾段收束全文", result["text"])
        self.assertIn("相关阅读", " ".join(result["noise"]))


if __name__ == "__main__":
    unittest.main()
