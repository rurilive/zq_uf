import unittest

from pagefinger import (
    fingerprint,
    similar,
    configure,
    configure_site,
    reset_config,
    register_ad_rule,
    unregister_ad_rule,
    FINGERPRINT_VERSION,
)


def _p(text):
    return "<p>%s</p>" % text


# 五段彼此不同、均超过最短块长的稳定正文，合计足够长，不触发短页面口径。
_COMMON = [
    _p("第一段足够长的正文内容用于稳定块哈希计算使用，需要凑够足够多的字数才行"),
    _p("第二段同样满足最短块长度要求的正文段落内容哦，也需要保持比较长的篇幅"),
    _p("第三段稳定的文章正文内容也需要超过八个字符才行，这里再补充更多的文字"),
    _p("第四段讲的是页面指纹需要剔除广告和脚本噪声，段落本身也要有足够的长度"),
    _p("第五段描述相似度按照块重合率而不是整页哈希来，最后再多写一些中文内容"),
]
_LONG_A = "".join(_COMMON)
_LONG_B = _LONG_A + _p("新增的结尾段落同样需要超过最短块长度限制才行，再补充若干汉字凑篇幅")


class FingerTest(unittest.TestCase):
    def test_same_page_same_hash(self):
        self.assertEqual(fingerprint("<p>a</p>")["hash"], fingerprint("<p>a</p>")["hash"])

    def test_whitespace_changes_hash(self):
        self.assertNotEqual(fingerprint("<p>a</p>")["hash"], fingerprint("<p> a </p>")["hash"])

    def test_different_pages_not_similar(self):
        self.assertFalse(similar("<p>a</p>", "<p>b</p>")["similar"])


class BlockFingerprintTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def tearDown(self):
        reset_config()

    def test_noise_blocks_are_stripped(self):
        base = "".join(_COMMON[:2])
        noisy = (
            base
            + "<script>window.track(123)</script>"
            + "<style>.banner{display:none}</style>"
            + "<div class='ads-banner'><p>广告位招商联系电话一二三四五六</p></div>"
            + "<ins class='adsbygoogle'></ins>"
        )
        fb, fn = fingerprint(base), fingerprint(noisy)
        # 脚本、样式、广告整体剔除，页面精确指纹与块指纹都不受影响。
        self.assertEqual(fb["hash"], fn["hash"])
        self.assertEqual(fb["block_hashes"], fn["block_hashes"])
        self.assertEqual(fb["blocks"], fn["blocks"])
        self.assertGreaterEqual(fn["stats"]["dropped_noise_blocks"], 3)

    def test_block_hashes_survive_whitespace_and_punctuation(self):
        a = _p("这是一段足够长的正文段落内容用于稳定分块测试")
        b = _p("  这是  一段足够长的正文段落内容，用于稳定分块测试！ ")
        fa, fb = fingerprint(a), fingerprint(b)
        self.assertEqual(fa["block_hashes"], fb["block_hashes"])
        # 空白/标点改动在块层面视为同一页。
        self.assertEqual(similar(a, b)["score"], 1.0)

    def test_stable_block_count_and_samples(self):
        fp = fingerprint(_COMMON[0] + "<p>短词</p>")
        self.assertEqual(fp["blocks"], 1)  # 短块被视为不稳定块丢弃
        self.assertEqual(len(fp["block_hashes"]), 1)
        self.assertTrue(fp["block_samples"][0]["text"])
        self.assertEqual(fp["block_samples"][0]["index"], 0)

    def test_custom_ad_rule_extension(self):
        base = _p("这是一段足够长的正文段落内容用于稳定分块测试")
        noisy = base + "<div class='marketing-slot'><p>营销推广文案内容超过八个字</p></div>"
        # 默认规则不认识 marketing-slot，噪声会进入指纹。
        self.assertNotEqual(fingerprint(base)["hash"], fingerprint(noisy)["hash"])

        def marketing_rule(tag, attrs):
            info = {k.lower(): (v or "") for k, v in attrs}
            tokens = info.get("class", "").lower().replace("-", " ").split()
            return "marketing" in tokens

        register_ad_rule(marketing_rule)
        try:
            self.assertEqual(fingerprint(base)["hash"], fingerprint(noisy)["hash"])
        finally:
            unregister_ad_rule(marketing_rule)
        # 注销后恢复原行为。
        self.assertNotEqual(fingerprint(base)["hash"], fingerprint(noisy)["hash"])

    def test_fingerprint_versioned_and_deterministic(self):
        html = "".join(_COMMON)
        fp1, fp2 = fingerprint(html), fingerprint(html)
        self.assertEqual(fp1, fp2)  # 同一页面多次计算完全一致
        self.assertEqual(fp1["version"], FINGERPRINT_VERSION)
        self.assertRegex(fp1["hash"], r"^[0-9a-f]{64}$")  # 版本域内的 SHA256
        self.assertRegex(fp1["block_hashes"][0], r"^[0-9a-f]{64}$")


class SimilarityTest(unittest.TestCase):
    def setUp(self):
        reset_config()

    def tearDown(self):
        reset_config()

    def test_near_duplicate_detected_by_block_overlap(self):
        result = similar(_LONG_A, _LONG_B)
        self.assertFalse(result["short_page"])
        self.assertTrue(result["similar"])
        self.assertEqual(result["matched_blocks"], 5)
        self.assertGreater(result["score"], 0.8)  # 5/6 ≈ 0.833
        self.assertLess(result["score"], 1.0)
        # 判定结果保留两侧块样本与命中样本。
        self.assertTrue(result["samples"]["a"])
        self.assertTrue(result["samples"]["b"])
        self.assertTrue(result["matched_samples"]["a"])
        self.assertTrue(result["matched_samples"]["b"])

    def test_disjoint_pages_zero_score(self):
        page_b = "".join([
            _p("完全不同主题的文章段落内容也是足够长的稳定块，再多写一些文字吧"),
            _p("两篇文章没有任何重合段落所以重合率应该是零，也需要凑够足够的长度"),
        ])
        result = similar(_LONG_A, page_b)
        self.assertFalse(result["similar"])
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["matched_blocks"], 0)
        self.assertEqual(result["matched_samples"]["a"], [])

    def test_short_page_flagged_and_threshold_lowered(self):
        short_a = _p("这是短页面里唯一的稳定正文块")
        short_b = short_a + _p("短页面第二段正文内容也超过八字限")
        result = similar(short_a, short_b)
        # 1/2 = 0.5：默认 0.75 不通过，短页面降到 0.5 后通过。
        self.assertTrue(result["short_page"])
        self.assertTrue(result["short_sides"]["a"])
        self.assertTrue(result["short_sides"]["b"])
        self.assertEqual(result["threshold"], 0.5)
        self.assertTrue(result["similar"])
        # 短页面置信度单独标注且明显低于长页面。
        long_conf = similar(_LONG_A, _LONG_A)["confidence"]
        self.assertLess(result["confidence"], long_conf)
        self.assertLess(result["confidence"], 1.0)

    def test_explicit_threshold_overrides_default(self):
        strict = similar(_LONG_A, _LONG_B, threshold=0.95)
        self.assertEqual(strict["threshold"], 0.95)
        self.assertFalse(strict["similar"])  # 0.833 < 0.95
        loose = similar(_LONG_A, _LONG_B, threshold=0.5)
        self.assertTrue(loose["similar"])

    def test_site_specific_threshold(self):
        configure_site("strict.example.com", threshold=0.95)
        result = similar(_LONG_A, _LONG_B, site="strict.example.com")
        self.assertEqual(result["threshold"], 0.95)
        self.assertFalse(result["similar"])
        # 未配置站点回退全局默认。
        self.assertEqual(similar(_LONG_A, _LONG_B, site="other.example")["threshold"], 0.75)

    def test_global_configure_changes_default_threshold(self):
        configure(threshold=0.8)
        self.assertEqual(similar(_LONG_A, _LONG_B)["threshold"], 0.8)
        self.assertTrue(similar(_LONG_A, _LONG_B)["similar"])

    def test_empty_input_is_stable(self):
        fp = fingerprint("")
        self.assertEqual(fp["blocks"], 0)
        self.assertTrue(fp["short"])
        self.assertEqual(fp["block_hashes"], [])
        self.assertFalse(similar("", "")["similar"])
