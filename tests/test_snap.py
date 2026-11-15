import unittest
from datetime import datetime, timedelta, timezone

from snapdiff import (
    MIN_RETENTION,
    SnapshotStore,
    diff,
    extract_blocks,
    normalize_text,
    snapshot,
)

BASE = """<html><head><title>示例</title><style>.a{color:red}</style></head>
<body>
<h1>站点标题</h1>
<p>第一段正文。</p>
<div class="ad-slot"><p>广告位内容</p></div>
<p>第二段<b>正文</b>。</p>
<p>浏览量 <span class="hit-counter">123</span> 次</p>
<script>var counter = 123</script>
</body></html>"""


def page(html=BASE, url="https://example.com/a", timestamp=None):
    data = {"html": html, "url": url}
    if timestamp is not None:
        data["timestamp"] = timestamp
    return data


class BlockTest(unittest.TestCase):
    def test_scripts_styles_ads_and_counters_are_not_blocks(self):
        blocks = extract_blocks(BASE)
        text = "".join(block["text"] for block in blocks)
        self.assertIn("第一段正文", text)
        self.assertNotIn("广告位内容", text)
        self.assertNotIn("var counter", text)
        self.assertNotIn("color:red", text)
        self.assertEqual([block["tag"] for block in blocks], ["h1", "p", "p", "p"])

    def test_fingerprint_ignores_markup_and_whitespace(self):
        plain = snapshot(page("<p>正文 内容</p>"))
        marked = snapshot(page("<p>\n  <b>正文</b> <em>内容</em>\n</p>"))
        self.assertEqual(plain["hash"], marked["hash"])
        self.assertEqual(plain["blocks"], marked["blocks"])

    def test_script_and_ad_and_counter_changes_keep_hash(self):
        base = snapshot(page())
        for html in (
            BASE.replace("var counter = 123", "var counter = 456"),
            BASE.replace("广告位内容", "另一条广告"),
            BASE.replace('class="hit-counter">123', 'class="hit-counter">999'),
        ):
            self.assertEqual(base["hash"], snapshot(page(html))["hash"])

    def test_similar_ads_outside_known_classes_are_ignored(self):
        html = '<p>正文</p><div data-ad-slot="123"><span>buy now</span></div>'
        snapshot_data = snapshot(page(html))
        self.assertEqual([block["text"] for block in snapshot_data["blocks"]], ["正文"])

    def test_normalize_applies_nfkc(self):
        self.assertEqual(normalize_text("Ａ　Ｂ"), "A B")

    def test_empty_page_has_no_blocks(self):
        self.assertEqual(snapshot({"html": ""})["blocks"], [])


class DiffTest(unittest.TestCase):
    def test_ad_only_change_is_not_an_alert(self):
        old = snapshot(page())
        new = snapshot(page(BASE.replace("广告位内容", "换了广告")))
        result = diff(old, new)
        self.assertFalse(result["changed"])
        self.assertEqual(result["changes"], [])
        self.assertFalse(result["alert"])

    def test_paragraph_change_is_located_with_summaries(self):
        changed = BASE.replace("第一段正文。", "第一段正文已经改写。")
        old = snapshot(page())
        new = snapshot(page(changed))
        result = diff(old, new)
        self.assertTrue(result["changed"])
        self.assertEqual(len(result["changes"]), 1)
        change = result["changes"][0]
        self.assertEqual(change["type"], "modified")
        self.assertEqual(change["paragraph"], 2)  # 第 1 块是标题
        self.assertEqual(change["before_summary"], "第一段正文。")
        self.assertEqual(change["after_summary"], "第一段正文已经改写。")
        self.assertIn("第一段正文已经改写", result["summary"])
        self.assertTrue(result["alert"])

    def test_insert_and_delete_are_reported(self):
        removed = BASE.replace("<p>第一段正文。</p>", "")
        added = BASE.replace("</body>", "<p>新增段落。</p></body>")
        old = snapshot(page())

        result = diff(old, snapshot(page(removed)))
        self.assertEqual([c["type"] for c in result["changes"]], ["removed"])
        self.assertEqual(result["changes"][0]["before_summary"], "第一段正文。")

        result = diff(old, snapshot(page(added)))
        self.assertEqual([c["type"] for c in result["changes"]], ["added"])
        self.assertEqual(result["changes"][0]["after_summary"], "新增段落。")

    def test_result_carries_version(self):
        store = SnapshotStore()
        old = snapshot(page(), store=store)
        new = snapshot(page(BASE.replace("第一段正文。", "改版正文。")), store=store)
        result = diff(old, new, store=store)
        self.assertEqual(result["old_version"], 1)
        self.assertEqual(result["version"], 2)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.store = SnapshotStore()
        self.url = "https://example.com/a"

    def add(self, html, timestamp):
        return snapshot(page(html, url=self.url, timestamp=timestamp), store=self.store)

    def test_versions_increase_and_are_retrievable_by_time(self):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        versions = [self.add(BASE.replace("第一段正文。", "v{}。".format(i)),
                             base + timedelta(days=i)) for i in range(4)]
        self.assertEqual([item["version"] for item in versions], [1, 2, 3, 4])
        self.assertEqual(self.store.get(self.url, 2)["blocks"], versions[1]["blocks"])
        window = self.store.history(self.url, since=base + timedelta(days=1),
                                    until=base + timedelta(days=2))
        self.assertEqual([item["version"] for item in window], [2, 3])
        self.assertEqual(self.store.as_of(self.url, base + timedelta(days=2, hours=1))["version"], 3)

    def test_unknown_version_raises(self):
        self.add(BASE, 0)
        with self.assertRaises(KeyError):
            self.store.get(self.url, 99)

    def test_prune_keeps_last_two_and_returns_removed(self):
        for i in range(5):
            self.add(BASE.replace("第一段正文。", "v{}。".format(i)), i)
        removed = self.store.prune(self.url)
        self.assertEqual(removed, [1, 2, 3])
        self.assertEqual([item["version"] for item in self.store.history(self.url)], [4, 5])
        self.assertGreaterEqual(self.store.prune(self.url, keep=1), [])

    def test_prune_never_drops_below_two_versions(self):
        for i in range(2):
            self.add(BASE.replace("第一段正文。", "v{}。".format(i)), i)
        self.assertEqual(self.store.prune(self.url, keep=0), [])
        self.assertEqual(len(self.store.history(self.url)), MIN_RETENTION)

    def test_false_positive_samples_survive_prune(self):
        for i in range(4):
            self.add(BASE.replace("第一段正文。", "v{}。".format(i)), i)
        self.store.mark_false_positive(self.url, 1)
        removed = self.store.prune(self.url)
        self.assertNotIn(1, removed)
        self.assertEqual(self.store.get(self.url, 1)["version"], 1)
        self.assertEqual([v["version"] for v in self.store.false_positive_samples(self.url)], [1])

    def test_alerts_disabled_but_results_kept(self):
        self.store.disable_alerts(self.url)
        old = self.add(BASE, 0)
        new = self.add(BASE.replace("第一段正文。", "改版正文。"), 1)
        result = diff(old, new, store=self.store)
        self.assertTrue(result["changed"])
        self.assertFalse(result["alert"])
        stored = self.store.results(self.url)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["changes"][0]["after_summary"], "改版正文。")

    def test_notifications_wait_for_one_week_of_observation(self):
        day0 = datetime(2026, 3, 1, tzinfo=timezone.utc)
        now = [day0]
        store = SnapshotStore(clock=lambda: now[0].timestamp())
        old = snapshot(page(BASE, url=self.url, timestamp=day0), store=store)
        store.enable_notifications(self.url)
        new = snapshot(page(BASE.replace("第一段正文。", "改版正文。"), url=self.url,
                            timestamp=day0 + timedelta(hours=1)), store=store)
        result = diff(old, new, store=store)
        self.assertTrue(result["alert"])          # 观察期内仍记录告警
        self.assertFalse(result["notify"])        # 但不自动通知
        self.assertFalse(store.notifications_active(self.url))

        now[0] = day0 + timedelta(days=7)
        self.assertTrue(store.notifications_active(self.url))
        newer = snapshot(page(BASE.replace("第一段正文。", "再改版。"), url=self.url,
                              timestamp=now[0]), store=store)
        result = diff(new, newer, store=store)
        self.assertTrue(result["notify"])

    def test_marking_false_positive_retracts_alert(self):
        day0 = datetime(2026, 3, 1, tzinfo=timezone.utc)
        old = self.add(BASE, day0)
        # 先制造一条正文告警，再把它标记成误报样本。
        new = snapshot(page(BASE.replace("第一段正文。", "第一段配图。"), url=self.url,
                            timestamp=day0 + timedelta(hours=1)), store=self.store)
        result = diff(old, new, store=self.store)
        self.assertTrue(result["alert"])

        self.store.mark_false_positive(self.url, new["version"])
        stored = self.store.results(self.url)[-1]
        self.assertTrue(stored["false_positive"])
        self.assertFalse(stored["alert"])
        self.assertFalse(stored["notify"])
        self.assertTrue(self.store.is_false_positive(self.url, new["version"]))


if __name__ == "__main__":
    unittest.main()
