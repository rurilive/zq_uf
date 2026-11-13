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


ARTICLE = """<html><head><title>深度报道</title>
<meta property="article:published_time" content="2026-11-01T08:00:00+08:00"></head>
<body>
<nav><a href="/">首页</a><a href="/news">新闻</a><a href="/about">关于我们</a></nav>
<article>
<h1>城市更新进入新阶段</h1>
<p>记者走访三地记录城市更新的最新进展，采访对象包括规划师与老街居民。</p>
<p>规划部门表示，下一阶段将公布更详细的改造方案，并向社会公开征求意见。</p>
</article>
<div class="related"><a href="/a">相关推荐：旧城区改造十年回顾</a></div>
<div id="comment"><p>网友：期待改造后的样子！</p></div>
<footer>版权所有 2026 某报社</footer>
</body></html>
"""

SHORT_PAGE = """<html><head><title>快讯</title></head>
<body><p>快讯：今日气温回升。</p><p>出行请注意防晒。</p></body></html>
"""

TABLE_PAGE = """<html><head><title>月度销售数据</title></head>
<body>
<p>以下是本月各地区销售数据汇总，包含销量与环比变化。</p>
<table>
<tr><th>地区</th><th>销量</th><th>环比</th></tr>
<tr><td>华东</td><td>1200</td><td>+12%</td></tr>
<tr><td>华南</td><td>860</td><td>-3%</td></tr>
</table>
<p>数据来源：公司内部统计。</p>
</body></html>
"""

DUPLICATE_PAGE = """<html><head><title>重复段落</title></head>
<body>
<p>这是第一段内容，长度足够通过常规阈值过滤。</p>
<p>这是第一段内容，长度足够通过常规阈值过滤。</p>
<p>这是第二段内容，与上一段不同。</p>
</body></html>
"""


class CleaningChainTest(unittest.TestCase):
    def test_nav_and_recommend_excluded(self):
        result = extract(ARTICLE)
        self.assertEqual(len(result["paragraphs"]), 3)
        joined = "".join(result["paragraphs"])
        self.assertNotIn("首页", joined)
        self.assertNotIn("相关推荐", joined)
        self.assertNotIn("网友", joined)
        self.assertNotIn("版权", joined)

    def test_article_metadata(self):
        result = extract(ARTICLE)
        self.assertEqual(result["title"], "深度报道")
        self.assertEqual(result["published"], "2026-11-01T08:00:00+08:00")
        self.assertTrue(0 < result["confidence"] < 1)

    def test_short_page_kept(self):
        result = extract(SHORT_PAGE)
        self.assertEqual(
            result["paragraphs"],
            ["快讯：今日气温回升。", "出行请注意防晒。"],
        )

    def test_short_page_threshold_configurable(self):
        result = extract(SHORT_PAGE, {"short_page_chars": 0})
        self.assertEqual(result["paragraphs"], [])

    def test_table_rows_preserved(self):
        result = extract(TABLE_PAGE)
        self.assertEqual(len(result["paragraphs"]), 5)
        self.assertIn("地区 | 销量 | 环比", result["paragraphs"])
        self.assertIn("华东 | 1200 | +12%", result["paragraphs"])

    def test_adjacent_duplicates_merged(self):
        result = extract(DUPLICATE_PAGE)
        self.assertEqual(len(result["paragraphs"]), 2)

    def test_dedupe_can_be_disabled(self):
        result = extract(DUPLICATE_PAGE, {"dedupe_adjacent": False})
        self.assertEqual(len(result["paragraphs"]), 3)
