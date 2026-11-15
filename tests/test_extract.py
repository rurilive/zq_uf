import tempfile
import unittest

from pagecore import (DEFAULT_CONFIG, MissingParagraphs, PageStore, blocks,
                      compare_batch, compare_text, extract, get_config,
                      is_failure, load_site_config, reset_site_configs,
                      save_site_config, set_site_config, split_paragraphs)

ARTICLE = """
<html><body>
<nav><a href="/">首页</a><a href="/hot">热门</a><a href="/about">关于</a></nav>
<header class="site-header"><h1>站点名</h1></header>
<main>
  <article class="post">
    <h1>文章标题</h1>
    <div class="byline">作者：张三 · 2026-01-01</div>
    <p>第一段正文，内容足够长，说明这篇到底在讲什么问题，为什么导航会被算进正文。</p>
    <p>第二段正文，说明内容密度和链接比例怎么参与判断，噪声块如何单独标出。</p>
    <p>第三段正文，说明规则如何按站点覆盖，站点改版时只调整配置不用改算法。</p>
    <footer class="post-tags"><a href="/t/1">标签一</a><a href="/t/2">标签二</a></footer>
  </article>
  <aside class="related"><h3>相关推荐</h3><ul><li><a href="/a">文章A</a></li></ul></aside>
  <section class="comments"><h3>评论区</h3><p>这条评论不属于正文，应当整块剔除。</p></section>
</main>
<footer class="site-footer">版权所有 © 2026</footer>
</body></html>
"""

LONG_ARTICLE = "<article>" + "".join(
    "<p>这是第%d段正文内容，长度足够，用来确认长文的所有段落都会被保留下来，"
    "不会因为某段分数略低就被丢掉。</p>" % i
    for i in range(8)
) + "</article>"


class ExtractTest(unittest.TestCase):
    def test_text_strips_tags(self):
        self.assertEqual(extract("<p>hello</p>")["text"], "hello")

    def test_navigation_is_noise(self):
        result = extract(ARTICLE)
        self.assertNotIn("首页", result["text"])
        self.assertNotIn("热门", result["text"])
        self.assertTrue(
            any(b["reason"] == "noise:tag=nav" for b in result["noise"])
        )

    def test_comments_and_related_are_noise(self):
        result = extract(ARTICLE)
        self.assertNotIn("这条评论不属于正文", result["text"])
        self.assertNotIn("文章A", result["text"])
        reasons = " ".join(b["reason"] for b in result["noise"])
        self.assertIn("comments", reasons)
        self.assertIn("related", reasons)

    def test_body_paragraphs_all_kept(self):
        result = extract(ARTICLE)
        for marker in ("第一段正文", "第二段正文", "第三段正文"):
            self.assertIn(marker, result["text"])
        self.assertEqual(result["blocks"], 4)  # 标题 + 三段正文

    def test_dropped_blocks_have_reasons(self):
        result = extract(LONG_ARTICLE)
        html = LONG_ARTICLE.replace("第3段", "第三段")
        self.assertEqual(result["blocks"], 8)
        self.assertEqual(result["dropped"], [])
        self.assertIn("第三段", extract(html)["text"])

    def test_short_article_kept_with_low_confidence(self):
        html = "<body><nav>首页 推荐</nav><div class='content'><p>只有两段。</p><p>短文也要保留。</p></div></body>"
        result = extract(html)
        self.assertIn("只有两段", result["text"])
        self.assertIn("短文也要保留", result["text"])
        self.assertTrue(result["short"])
        self.assertLess(result["confidence"], 0.6)
        self.assertEqual(result["page_type"], "short")

    def test_long_article_high_confidence(self):
        result = extract(LONG_ARTICLE)
        self.assertEqual(result["confidence"], 1.0)
        self.assertFalse(result["short"])
        self.assertFalse(result["fallback"])
        self.assertEqual(result["page_type"], "article")

    def test_list_page_not_treated_as_article(self):
        html = "<body><div class='list'>" + "".join(
            "<div class='item'><a href='/%d'>标题%d，这是列表项</a></div>" % (i, i)
            for i in range(20)
        ) + "</div></body>"
        result = extract(html)
        self.assertEqual(result["text"], "")
        self.assertEqual(result["page_type"], "list")
        self.assertEqual(result["confidence"], 0.0)
        self.assertTrue(all(b["link_ratio"] >= 0.5 for b in result["dropped"]))

    def test_kept_and_noise_have_locators(self):
        result = extract(ARTICLE)
        for block in result["kept"]:
            self.assertIn(">", block["locator"])
            self.assertTrue(block["keep"])
        for block in result["noise"]:
            self.assertFalse(block["keep"])
            self.assertTrue(block["reason"])
            self.assertIn(">", block["locator"])

    def test_blocks_exposes_every_block_with_kind(self):
        result = blocks(ARTICLE)
        kinds = {b["kind"] for b in result}
        self.assertEqual(kinds, {"content", "noise"})
        kept = [b for b in result if b["keep"]]
        self.assertTrue(kept)
        self.assertTrue(all(b["kind"] == "content" for b in kept))

    def test_malformed_html_still_parses(self):
        html = "<div class=content><p>第一段<p>第二段<ul><li>一<li>二</ul><p>第三段"
        result = extract(html)
        self.assertIn("第一段", result["text"])
        self.assertIn("第三段", result["text"])

    def test_script_and_style_never_in_text(self):
        html = "<article><p>正文</p><script>var x=1</script><style>.a{}</style></article>"
        self.assertEqual(extract(html)["text"], "正文")

    def test_head_title_not_in_body(self):
        html = "<html><head><title>页面标题</title></head><body><p>正文</p></body></html>"
        self.assertEqual(extract(html)["text"], "正文")

    def test_article_internal_header_footer_kept(self):
        # article 内部的 header/footer 是文章结构，不是站点框架
        html = (
            "<main><article><header><h1>标题</h1></header>"
            "<p>正文内容，长度足够参与测试的判断。</p>"
            "<footer><p>文末说明，属于本文内容。</p></footer></article>"
            "<footer>版权所有</footer></main>"
        )
        result = extract(html)
        self.assertIn("标题", result["text"])
        self.assertIn("文末说明", result["text"])
        self.assertNotIn("版权所有", result["text"])

    def test_ul_list_page_detected_as_list(self):
        html = "<body><ul>" + "".join(
            "<li>标题%d，这是一个列表项</li>" % i for i in range(12)
        ) + "</ul></body>"
        result = extract(html)
        self.assertEqual(result["page_type"], "list")
        self.assertLess(result["confidence"], 0.4)

    def test_bullet_list_inside_article_is_not_list_page(self):
        html = (
            "<article>" + "".join(
                "<p>第%d段正文，内容足够长，用来确认正文里的要点列表不影响页面类型。</p>" % i
                for i in range(4)
            ) + "<ul><li>要点一</li><li>要点二</li></ul></article>"
        )
        result = extract(html)
        self.assertNotEqual(result["page_type"], "list")

    def test_short_mode_requires_few_blocks(self):
        # 块多但每段短，是正常文章，不算“短正文”
        html = "<article>" + "".join(
            "<p>第%d段短句。</p>" % i for i in range(10)
        ) + "</article>"
        result = extract(html)
        self.assertFalse(result["short"])

    def test_empty_page_reports_empty(self):
        result = extract("<html><body><nav>菜单</nav></body></html>")
        self.assertEqual(result["text"], "")
        self.assertEqual(result["page_type"], "empty")
        self.assertTrue(is_failure(result))


class SiteConfigTest(unittest.TestCase):
    def setUp(self):
        reset_site_configs()

    def tearDown(self):
        reset_site_configs()

    def test_site_override_removes_new_noise_container(self):
        html = (
            "<body><div class='partnerbox'><p>合作推广位，改版后新增的容器。</p></div>"
            "<article><p>第一段正文，长度足够参与测试。</p>"
            "<p>第二段正文，长度也足够参与测试。</p></article></body>"
        )
        set_site_config("example.com", noise_hints=["partnerbox"])
        result = extract(html, site="https://www.example.com/post/1")
        self.assertNotIn("合作推广位", result["text"])
        self.assertIn("第一段正文", result["text"])
        self.assertEqual(result["site"], "example.com")

    def test_override_is_per_site(self):
        html = "<body><div class='partnerbox'><p>合作推广位，改版后新增的容器。</p></div><article><p>正文内容一段。</p></article></body>"
        set_site_config("example.com", noise_hints=["partnerbox"])
        self.assertIn("合作推广位", extract(html, site="other.com")["text"])

    def test_overrides_argument_does_not_touch_global_config(self):
        html = "<body><div class='feed-hot'><p>热门推荐</p></div><p>正文</p></body>"
        extract(html, overrides={"noise_hints": ["feed-hot"]})
        self.assertNotIn("feed-hot", get_config()["noise_hints"])

    def test_thresholds_can_be_tuned_per_site(self):
        html = "<article><p>短正文。</p></article>"
        strict = extract(html, overrides={"short_body_chars": 5})
        self.assertEqual(strict["page_type"], "short")
        loose = extract(
            html,
            overrides={"short_body_chars": 1, "short_min_chars": 1},
        )
        self.assertIn("短正文", loose["text"])

    def test_subdomain_falls_back_to_registered_domain(self):
        set_site_config("example.com", keep_score=0.1)
        self.assertEqual(get_config("news.example.com")["keep_score"], 0.1)
        self.assertEqual(get_config("example.org")["keep_score"],
                         DEFAULT_CONFIG["keep_score"])

    def test_save_and_load_site_config(self):
        set_site_config("example.com", noise_hints=["feed-hot"])
        with tempfile.TemporaryDirectory() as directory:
            path = directory + "/sites.json"
            save_site_config(path)
            reset_site_configs()
            self.assertNotIn("feed-hot", get_config("example.com")["noise_hints"])
            load_site_config(path)
            self.assertIn("feed-hot", get_config("example.com")["noise_hints"])


class PageStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 1_700_000_000.0
        self.store = PageStore(self.temp.name, retention_days=7,
                               clock=lambda: self.now)
        self.key = "https://example.com/post/1"
        self.v1 = self._article("第一段正文内容，长度足够用于测试。",
                                "第二段正文内容，长度也足够用于测试。")
        self.v2 = self._article(
            "第一段正文内容，长度足够用于测试。",
            "第二段正文内容，长度也足够用于测试。",
            "第三段正文，是改版后新增的内容。",
        )
        self.broken = "<html><body><div class='x'></div></body></html>"

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _article(*paragraphs):
        return "<html><body><nav>首页 推荐</nav><article>" + "".join(
            "<p>%s</p>" % p for p in paragraphs
        ) + "</article></body></html>"

    def test_first_extract_saves_current(self):
        result = self.store.extract_safe(self.key, self.v1)
        self.assertFalse(result["stale"])
        self.assertIn("第一段正文", self.store.current(self.key)["text"])

    def test_failure_keeps_previous_result(self):
        self.store.extract_safe(self.key, self.v1)
        previous = self.store.current(self.key)["text"]
        result = self.store.extract_safe(self.key, self.broken)
        self.assertTrue(result["stale"])
        self.assertEqual(result["reason"], "extract-failed")
        self.assertEqual(self.store.current(self.key)["text"], previous)
        self.assertEqual(result["candidate"]["text"], "")

    def test_forced_overwrite_after_human_confirmation(self):
        self.store.extract_safe(self.key, self.v1)
        result = self.store.extract_safe(self.key, self.broken, force=True)
        self.assertFalse(result["stale"])
        self.assertEqual(result["reason"], "forced-overwrite")
        self.assertEqual(self.store.current(self.key)["text"], "")

    def test_failure_without_previous_does_not_save(self):
        result = self.store.extract_safe(self.key, self.broken)
        self.assertTrue(result["stale"])
        self.assertIsNone(result["result"])
        self.assertEqual(result["reason"], "extract-failed-no-previous")
        self.assertIsNone(self.store.current(self.key))

    def test_stage_then_diff_detects_lost_paragraphs(self):
        self.store.extract_safe(self.key, self.v1)
        self.store.stage(self.key, self._article("第一段正文内容，长度足够用于测试。"))
        diff = self.store.diff(self.key)
        self.assertEqual(len(diff["missing"]), 1)
        self.assertIn("第二段", diff["missing"][0])
        self.assertLess(diff["retained"], 1.0)

    def test_diff_reports_added_paragraphs(self):
        self.store.extract_safe(self.key, self.v1)
        self.store.stage(self.key, self._article(
            "第一段正文内容，长度足够用于测试。",
            "第二段正文内容，长度也足够用于测试。",
            "第三段是改版后新增的正文。",
        ))
        diff = self.store.diff(self.key)
        self.assertEqual(diff["missing"], [])
        self.assertEqual(len(diff["added"]), 1)

    def test_promote_refuses_when_paragraphs_lost(self):
        self.store.extract_safe(self.key, self.v1)
        self.store.stage(self.key, self._article("只剩这一段正文了，其他段落都没有了。"))
        with self.assertRaises(MissingParagraphs):
            self.store.promote(self.key)
        self.assertIn("第一段", self.store.current(self.key)["text"])

    def test_promote_after_confirmation(self):
        self.store.extract_safe(self.key, self.v1)
        self.store.stage(self.key, self._article("新正文，旧段落确实被删掉了。"))
        snapshot = self.store.promote(self.key, require_no_missing=False)
        self.assertEqual(snapshot["source"], "promote")
        self.assertIn("新正文", self.store.current(self.key)["text"])
        self.assertIsNone(self.store.staged(self.key))

    def test_promote_without_loss(self):
        self.store.extract_safe(self.key, self.v1)
        self.store.stage(self.key, self.v2)
        snapshot = self.store.promote(self.key)
        self.assertIn("改版后", snapshot["text"])

    def test_rollback_restores_previous_body(self):
        self.store.extract_safe(self.key, self.v1)
        old_text = self.store.current(self.key)["text"]
        self.store.stage(self.key, self.v2)
        self.store.promote(self.key)
        self.assertNotEqual(self.store.current(self.key)["text"], old_text)
        restored = self.store.rollback(self.key)
        self.assertEqual(restored["source"], "rollback")
        self.assertEqual(restored["text"], old_text)

    def test_history_retained_within_week(self):
        self.store.extract_safe(self.key, self.v1)
        self.now += 3 * 86400
        self.store.extract_safe(self.key, self.v2)
        self.assertEqual(len(self.store.history(self.key)), 1)

    def test_history_pruned_after_retention(self):
        self.store.extract_safe(self.key, self.v1)
        self.now += 1
        self.store.extract_safe(self.key, self.v2)
        self.assertEqual(len(self.store.history(self.key)), 1)
        self.now += 8 * 86400
        self.store.prune(self.key)
        self.assertEqual(self.store.history(self.key), [])

    def test_stage_batch_flags_lossy_pages(self):
        from pagecore import stage_batch
        self.store.extract_safe("a", self.v1)
        self.store.extract_safe("b", self.v1)
        report = stage_batch(self.store, [
            ("a", self.v2),
            ("b", self._article("整页都换了，旧段落全部消失。")),
        ])
        self.assertEqual(report["pages"], 2)
        self.assertEqual(report["lossy_pages"], 1)
        self.assertFalse(report["safe"])
        self.assertEqual(report["details"][1]["key"], "b")


class CompareTest(unittest.TestCase):
    def test_identical_text(self):
        diff = compare_text("第一段\n\n第二段", "第一段\n\n第二段")
        self.assertEqual(diff["missing"], [])
        self.assertEqual(diff["added"], [])
        self.assertFalse(diff["changed"])

    def test_missing_paragraph(self):
        diff = compare_text("第一段\n\n第二段", "第一段")
        self.assertEqual(diff["missing"], ["第二段"])

    def test_sentence_split_for_long_paragraphs(self):
        long_text = "句子一。" * 60
        paragraphs = split_paragraphs(long_text)
        self.assertGreater(len(paragraphs), 1)

    def test_batch_summary(self):
        report = compare_batch([
            ("第一段\n\n第二段", "第一段\n\n第二段"),
            ("甲\n\n乙", "甲"),
        ])
        self.assertEqual(report["pages"], 2)
        self.assertEqual(report["lossy_pages"], 1)
        self.assertEqual(report["missing"], 1)


if __name__ == "__main__":
    unittest.main()
