import unittest

from langdetect2 import (
    SiteStats, apply_override, detect, evaluate_onboarding, is_indexable,
    review_page,
)

EN = ("The quick brown fox jumps over the lazy dog. This is an English "
      "paragraph with enough words for the system to make a decision today.")
ZH = "这是一个中文段落，需要通过汉字字符范围来识别语言。系统应该支持多语言页面的正确分流处理和人工复核流程。"


class OverrideTest(unittest.TestCase):
    def test_override_keeps_original_result(self):
        original = detect({"text": ZH})
        overridden = apply_override(original, "ja", reviewer="bob",
                                    note="日语文言体")
        self.assertEqual(overridden["language"], "ja")
        self.assertEqual(overridden["confidence"], "high")
        self.assertEqual(overridden["confidence_reason"], "human-override")
        # 原识别结果完整保留
        self.assertEqual(overridden["override"]["original_language"],
                         original["language"])
        self.assertEqual(overridden["override"]["original_confidence"],
                         original["confidence"])
        self.assertEqual(overridden["override"]["reviewer"], "bob")
        # 原结果对象不被修改
        self.assertNotIn("override", original)

    def test_review_page_helper(self):
        result = review_page({"text": "xyz qwrt"}, "en", reviewer="lee")
        self.assertEqual(result["language"], "en")
        self.assertIn("override", result)

    def test_indexable_rules(self):
        high = detect({"text": EN})
        low = detect({"text": "xyz qwrt zxpv bnml"})
        self.assertTrue(is_indexable(high))
        self.assertFalse(is_indexable(low))
        self.assertTrue(is_indexable(apply_override(low, "en")))


class StatsTest(unittest.TestCase):
    def _site_pages(self, site, n_en, n_low):
        pages = []
        for i in range(n_en):
            pages.append(detect({"url": f"http://{s}/p{i}" if False
                                 else f"http://{site}/p{i}", "text": EN}))
        for i in range(n_low):
            pages.append(detect({"url": f"http://{site}/low{i}",
                                 "text": "xyz qwrt"}))
        return pages

    def test_summary_grouped_by_site(self):
        stats = SiteStats()
        for r in self._site_pages("a.example.com", 9, 1):
            stats.add(r)
        for r in self._site_pages("b.example.com", 5, 0):
            stats.add(r)
        summary = stats.summary()
        self.assertEqual(set(summary), {"a.example.com", "b.example.com"})
        self.assertEqual(summary["a.example.com"]["pages"], 10)
        self.assertEqual(summary["a.example.com"]["dominant_language"], "en")
        self.assertEqual(summary["a.example.com"]["low_confidence_rate"], 0.1)
        self.assertEqual(stats.pages, 15)

    def test_override_rate(self):
        stats = SiteStats()
        low = detect({"url": "http://s.example.com/x",
                      "text": "xyz qwrt zxpv"})
        stats.add(low)
        stats.add(apply_override(low, "en"))
        s = stats.summary("s.example.com")
        self.assertEqual(s["override_rate"], 0.5)

    def test_multilingual_rate(self):
        stats = SiteStats()
        stats.add(detect({"url": "http://s.example.com/m",
                          "text": EN + "\n\n" + ZH}))
        s = stats.summary("s.example.com")
        self.assertEqual(s["multilingual_rate"], 1.0)


class OnboardingTest(unittest.TestCase):
    def test_stable_site_passes(self):
        results = [
            detect({"url": f"http://new.example.com/{i}", "text": EN})
            for i in range(20)
        ]
        report = evaluate_onboarding(results, expected_language="en")
        self.assertTrue(report["ready"])
        self.assertTrue(report["checks"]["matches_expected"])
        self.assertTrue(report["checks"]["dominant_stable"])

    def test_unstable_site_blocked(self):
        results = []
        for i in range(10):
            results.append(detect({"url": f"http://new.example.com/{i}",
                                   "text": EN}))
            results.append(detect({"url": f"http://new.example.com/z{i}",
                                   "text": ZH}))
        report = evaluate_onboarding(results, expected_language="en")
        self.assertFalse(report["ready"])
        self.assertFalse(report["checks"]["dominant_stable"])

    def test_insufficient_samples_blocked(self):
        results = [
            detect({"url": f"http://new.example.com/{i}", "text": EN})
            for i in range(5)
        ]
        report = evaluate_onboarding(results)
        self.assertFalse(report["ready"])
        self.assertFalse(report["checks"]["enough_samples"])

    def test_high_low_confidence_rate_blocked(self):
        results = [
            detect({"url": f"http://new.example.com/{i}", "text": EN})
            for i in range(17)
        ]
        for i in range(3):
            results.append(detect({"url": f"http://new.example.com/l{i}",
                                   "text": "xyz qwrt"}))
        report = evaluate_onboarding(results, expected_language="en")
        self.assertFalse(report["ready"])
        self.assertFalse(report["checks"]["low_confidence_controlled"])

    def test_rejects_multiple_sites(self):
        results = [
            detect({"url": "http://a.example.com/1", "text": EN}),
            detect({"url": "http://b.example.com/1", "text": EN}),
        ]
        with self.assertRaises(ValueError):
            evaluate_onboarding(results)


if __name__ == "__main__":
    unittest.main()
