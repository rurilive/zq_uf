import unittest

from changedetect import diff, should_ingest

BASE = (
    '<html><body>'
    '<div id="product">'
    '<h1 class="title">机械键盘</h1>'
    '<p class="price">¥199</p>'
    '<p class="stock">现货</p>'
    '</div>'
    '<div class="footer">客服 10000</div>'
    '<div class="ad-slot">广告A</div>'
    '<script>track(1)</script>'
    '</body></html>'
)


class MoveTest(unittest.TestCase):
    def test_move_not_delete_insert(self):
        moved = BASE.replace(
            '<p class="price">¥199</p><p class="stock">现货</p>',
            '<p class="stock">现货</p><p class="price">¥199</p>',
        )
        d = diff(BASE, moved)
        self.assertEqual(1, d["moves"])
        self.assertEqual(0, d["inserts"])
        self.assertEqual(0, d["deletes"])
        self.assertEqual([], d["fields"])

    def test_move_is_layout_grade(self):
        moved = BASE.replace(
            '<p class="price">¥199</p><p class="stock">现货</p>',
            '<p class="stock">现货</p><p class="price">¥199</p>',
        )
        d = diff(BASE, moved)
        self.assertEqual("layout", d["grade"])
        self.assertFalse(should_ingest(d))
        self.assertTrue(d["audit"])


class NoiseTest(unittest.TestCase):
    def test_script_style_ignored(self):
        changed_script = BASE.replace("track(1)", "track(2)").replace(
            "<script>", "<script><style>.a{color:red}</style>"
        )
        self.assertFalse(diff(BASE, changed_script)["changed"])

    def test_ad_region_ignored(self):
        changed_ad = BASE.replace("广告A", "广告B")
        d = diff(BASE, changed_ad)
        self.assertFalse(d["changed"])
        self.assertFalse(should_ingest(d))


class FieldTest(unittest.TestCase):
    def test_field_merged_with_old_new_location(self):
        old = '<div id="p"><p class="price">¥<span>199</span></p></div>'
        new = '<div id="p"><p class="price">¥<span>189</span></p></div>'
        d = diff(old, new)
        self.assertEqual(1, len(d["fields"]))
        field = d["fields"][0]
        self.assertEqual("price", field["field"])
        self.assertEqual("199", field["old"])
        self.assertEqual("189", field["new"])
        self.assertIn("p.price", field["location"])


class GradeTest(unittest.TestCase):
    def test_chrome_change_is_content_grade(self):
        changed = BASE.replace("客服 10000", "客服 20000")
        d = diff(BASE, changed)
        self.assertEqual("content", d["grade"])
        self.assertFalse(should_ingest(d))

    def test_business_change_ingests(self):
        changed = BASE.replace("¥199", "¥179")
        d = diff(BASE, changed)
        self.assertEqual("business", d["grade"])
        self.assertTrue(should_ingest(d))
        self.assertEqual(1, d["changed_nodes"])

    def test_identical_is_none_grade(self):
        d = diff(BASE, BASE)
        self.assertEqual("none", d["grade"])
        self.assertEqual(0, d["changed_nodes"])
        self.assertFalse(should_ingest(d))


if __name__ == "__main__":
    unittest.main()
