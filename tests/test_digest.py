import unittest

from contenthash import (
    DEFAULT_THRESHOLD,
    SHORT_THRESHOLD,
    fingerprint,
    normalize_text,
    similar,
)


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

    def test_template_noise_keeps_stable_hash(self):
        left = (
            "<nav>home about login</nav>"
            "<header>site title</header>"
            "<div class=\"ad-banner\">big sale 90% off</div>"
            "<article><p>real paragraph one</p><p>real paragraph two</p></article>"
            "<footer>copyright links</footer>"
        )
        right = (
            "<nav>home about logout profile</nav>"
            "<header>other title</header>"
            "<div id=\"googleads\">another promo slot</div>"
            "<article><p>real paragraph one</p><p>real paragraph two</p></article>"
            "<footer>new copyright links</footer>"
        )
        fp_left, fp_right = fingerprint(left), fingerprint(right)
        # 稳定正文指纹一致，块里不含导航/广告/页眉页脚内容。
        self.assertEqual(fp_left.hash, fp_right.hash)
        self.assertEqual(fp_left.blocks, ("real paragraph one", "real paragraph two"))
        self.assertEqual(fp_left.block_count, 2)
        # 原始页面仍不同，且整页 raw_hash 有区分度。
        self.assertNotEqual(fp_left, fp_right)
        self.assertNotEqual(fp_left.raw_hash, fp_right.raw_hash)
        result = similar(left, right)
        self.assertTrue(result)
        self.assertEqual(result.matched, 2)
        self.assertEqual(result.score, 1.0)

    def test_script_and_style_are_removed(self):
        left = "<script>var x = 1;</script><style>.a{color:red}</style><p>body text</p><p>second body</p>"
        right = "<script>track();</script><style>.b{color:blue}</style><p>body text</p><p>second body</p>"
        self.assertEqual(fingerprint(left).hash, fingerprint(right).hash)

    def test_punctuation_and_whitespace_normalized(self):
        self.assertEqual(
            normalize_text("Hello,  world！你好。 "),
            normalize_text("Hello world 你好"),
        )
        left = "<p>Hello, world ...</p><p>Second; paragraph!</p>"
        right = "<p>  Hello  world </p><p>Second paragraph</p>"
        fp_left, fp_right = fingerprint(left), fingerprint(right)
        self.assertEqual(fp_left.hash, fp_right.hash)
        self.assertTrue(similar(left, right))

    def test_short_body_lower_threshold_and_confidence(self):
        # 两个稳定块的短正文，一块相同一块不同：Jaccard = 1/3，低于短阈值不判重。
        left = "<p>shared body block</p><p>left only block</p>"
        right = "<p>shared body block</p><p>right only block</p>"
        result = similar(left, right)
        self.assertTrue(result.short)
        self.assertEqual(result.threshold, SHORT_THRESHOLD)
        self.assertLess(SHORT_THRESHOLD, DEFAULT_THRESHOLD)
        self.assertFalse(result)
        # 单块短正文证据不足：即使完全重合也不判近重复，只给低置信度。
        single = similar("<p>lone paragraph</p><nav>x</nav>",
                         "<p>lone paragraph</p><nav>y</nav>")
        self.assertFalse(single)
        self.assertEqual(single.confidence, 0.25)
        self.assertIn("证据不足", single.reason)
        # 置信度随块数上升并封顶。
        self.assertEqual(fingerprint("<p>a</p>").confidence, 0.25)
        long_fp = fingerprint("".join(f"<p>block {i} content</p>" for i in range(5)))
        self.assertEqual(long_fp.confidence, 1.0)

    def test_threshold_is_adjustable(self):
        # 5 个稳定块（非短正文）：4 块重合，Jaccard = 4/6 ≈ 0.667。
        left = "".join(f"<p>paragraph number {i} here</p>" for i in range(5))
        right = "".join(
            [f"<p>paragraph number {i} here</p>" for i in range(4)]
            + ["<p>totally different final block</p>"]
        )
        strict = similar(left, right, threshold=0.9)
        relaxed = similar(left, right, threshold=0.5)
        self.assertFalse(strict.short)
        self.assertEqual(strict.threshold, 0.9)
        self.assertFalse(strict)
        self.assertEqual(relaxed.threshold, 0.5)
        self.assertTrue(relaxed)
        self.assertEqual(relaxed.matched, 4)
        self.assertIn("阈值 0.50", relaxed.reason)

    def test_result_exposes_fingerprint_and_evidence(self):
        fp = fingerprint("<nav>m</nav><p>one paragraph</p><p>two paragraph</p>")
        self.assertEqual(len(fp.hash), 64)
        self.assertEqual(len(fp.raw_hash), 64)
        self.assertEqual(fp.block_hashes.__class__.__name__, "tuple")
        result = similar(fp, fingerprint("<p>one paragraph</p><p>two paragraph</p>"))
        self.assertEqual(result.block_count_a, result.block_count_b)
        self.assertTrue(result.reason)
