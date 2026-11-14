import unittest

from htmlcanon import canonical, same, analyze


class CanonTest(unittest.TestCase):
    def test_strip(self):
        self.assertEqual(canonical(" <p>a</p> "), "<p>a</p>")

    def test_same_after_strip(self):
        self.assertTrue(same("<p>a</p>", " <p>a</p> "))

    # 说明：旧版此处断言“属性顺序不同则不等价”，正是本次改造要修复的问题——
    # 新规范要求属性白名单 + 按名排序，同内容不同写法必须等价，故翻转该断言。
    def test_attr_order_normalized(self):
        self.assertTrue(
            same('<p id="1" class="x">a</p>', '<p class="x" id="1">a</p>')
        )
        self.assertEqual(
            canonical('<p id="1" class="x">a</p>'),
            canonical('<p class="x" id="1">a</p>'),
        )


class AttrWhitelistTest(unittest.TestCase):
    def test_non_whitelisted_attrs_dropped(self):
        out = canonical('<p id="keep" data-x="1" onclick="evil()" class="a">t</p>')
        self.assertIn('id="keep"', out)
        self.assertIn('class="a"', out)
        self.assertNotIn("data-x", out)
        self.assertNotIn("onclick", out)

    def test_attrs_sorted_and_boolean(self):
        out = canonical('<input type="text" name="f" disabled="disabled">')
        self.assertEqual(out, '<input disabled name="f" type="text">')

    def test_class_dedup_and_sorted(self):
        out = canonical('<p class="b a b c">t</p>')
        self.assertIn('class="a b c"', out)

    def test_whitelist_hit_recorded(self):
        result = analyze('<div onclick="x()" id="d">t</div>')
        dropped = [h for h in result.rules if h.rule == "attr-dropped"]
        self.assertEqual([h.attr for h in dropped], ["onclick"])


class HiddenNodeTest(unittest.TestCase):
    def test_hidden_attr_removed(self):
        self.assertEqual(canonical('<div hidden>secret</div><p>shown</p>'), "<p>shown</p>")

    def test_input_hidden_removed(self):
        out = canonical('<input type="hidden" name="t" value="1"><input name="v">')
        self.assertNotIn("hidden", out)
        self.assertIn('name="v"', out)

    def test_css_hidden_removed(self):
        out = canonical('<p style="display: none;">a</p><p>b</p>')
        self.assertEqual(out, "<p>b</p>")
        out2 = canonical('<span style="visibility:hidden">x</span>')
        self.assertEqual(out2, "")

    def test_aria_hidden_removed(self):
        out = canonical('<span aria-hidden="true">x</span><span>y</span>')
        self.assertEqual(out, "<span>y</span>")

    def test_hidden_rule_hits(self):
        result = analyze('<div hidden>x</div>')
        self.assertIn("drop-hidden", result.rule_counts)


class NoiseRemovalTest(unittest.TestCase):
    def test_script_style_comment_removed(self):
        html = '<div><!-- note --><style>a{color:red}</style>' \
               '<script>alert(1)</script>text</div>'
        self.assertEqual(canonical(html), "<div>text</div>")
        result = analyze(html)
        self.assertIn("drop-script", result.rule_counts)
        self.assertIn("drop-style", result.rule_counts)
        self.assertIn("drop-comment", result.rule_counts)

    def test_tag_case_and_whitespace_normalized(self):
        a = "<DIV  CLASS='x'>Hello   <SPAN>w</SPAN></DIV>"
        b = '<div class="x">Hello <span>w</span></div>'
        self.assertEqual(canonical(a), canonical(b))

    def test_self_closing_normalized(self):
        self.assertEqual(canonical("line<br/>end"), "line<br>end")
        self.assertEqual(canonical("<img src='a.png' />"), '<img src="a.png">')

    def test_entities_round_trip(self):
        self.assertEqual(canonical("<p>a &amp; b</p>"), "<p>a &amp; b</p>")

    def test_inline_word_boundary_space_kept(self):
        # 行内元素两侧的单空格是词边界，不能丢；块边界空白要剥离。
        self.assertEqual(canonical("<p>a <b>b</b> c</p>"), "<p>a <b>b</b> c</p>")
        self.assertEqual(canonical("<ul> <li>x</li> <li>y</li> </ul>"),
                         "<ul><li>x</li><li>y</li></ul>")
        self.assertEqual(canonical("<p>x</p> <p>y</p>"), "<p>x</p><p>y</p>")

    def test_attr_value_trimmed_and_ideographic_space_kept(self):
        self.assertEqual(
            canonical('<a title="  a   b  ">x</a>'),
            '<a title="a b">x</a>',
        )
        # 全角空格不是 HTML 空白，必须原样保留。
        self.assertIn("　", canonical("<p>a　b</p>"))


class BlockFingerprintTest(unittest.TestCase):
    VARIANT_A = '<article><h1>Title</h1><p>body  text</p></article><nav>menu</nav>'
    VARIANT_B = '<ARTICLE><H1>Title</H1><P>body   text</P></ARTICLE><NAV>menu</NAV>'

    def test_block_fingerprints_stable_across_variants(self):
        ra = analyze(self.VARIANT_A)
        rb = analyze(self.VARIANT_B)
        self.assertEqual(
            [b.fingerprint for b in ra.blocks],
            [b.fingerprint for b in rb.blocks],
        )
        self.assertEqual([b.tag for b in ra.blocks], [b.tag for b in rb.blocks])

    def test_block_change_isolates_fingerprint(self):
        base = analyze('<article><h1>Title</h1><p>body</p></article>')
        changed = analyze('<article><h1>Title</h1><p>other</p></article>')
        self.assertEqual(base.blocks[0].tag, "article")
        self.assertNotEqual(
            base.blocks[0].fingerprint, changed.blocks[0].fingerprint
        )

    def test_block_source_traceback(self):
        html = '<nav>menu</nav><article>body</article>'
        result = analyze(html)
        nav = next(b for b in result.blocks if b.tag == "nav")
        start, end = result.source_of(nav.index)
        self.assertEqual(html[start:end], "<nav>menu</nav>")


class EquivalenceClassTest(unittest.TestCase):
    def test_same_content_variants_one_class(self):
        a = '<div id="x" class="a b">Hello   world</div>'
        b = '<DIV class="b a" ID="x">Hello world</DIV>'
        ra, rb = analyze(a), analyze(b)
        self.assertTrue(same(a, b))
        self.assertEqual(ra.equivalence_class, rb.equivalence_class)
        self.assertEqual(ra.fingerprint, rb.fingerprint)

    def test_different_content_other_class(self):
        ra = analyze("<p>one</p>")
        rb = analyze("<p>two</p>")
        self.assertFalse(same("<p>one</p>", "<p>two</p>"))
        self.assertNotEqual(ra.equivalence_class, rb.equivalence_class)

    def test_nav_ad_does_not_pollute_article_fingerprint(self):
        # 正文一致，导航/广告等噪声块不同：正文块自身指纹保持稳定。
        page_a = '<nav>home login</nav><article><p>story</p></article>'
        page_b = '<nav>home signup ad</nav><article><p>story</p></article>'
        article_a = next(b for b in analyze(page_a).blocks if b.tag == "article")
        article_b = next(b for b in analyze(page_b).blocks if b.tag == "article")
        self.assertEqual(article_a.fingerprint, article_b.fingerprint)


if __name__ == "__main__":
    unittest.main()
