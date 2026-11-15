import shutil
import tempfile
import unittest

from snapdiff import SnapshotStore, diff, mark_false_positive, snapshot

PAGE = """
<html><body>
<h1>每日要闻</h1>
<div class="ad-banner">广告位A</div>
<p>第一条：要闻正文。</p>
<p>第二条：天气预报，晴。</p>
<div class="counter">访问次数 100</div>
<script>track(1)</script>
<style>.ad-banner{color:red}</style>
</body></html>
"""


def noisy_variant(html):
    """只动噪声：广告轮换、计数器+1、脚本/样式变化。"""
    return html.replace("广告位A", "广告位B") \
               .replace("访问次数 100", "访问次数 101") \
               .replace("track(1)", "track(2)") \
               .replace("color:red", "color:blue")


class NoiseStripTest(unittest.TestCase):
    def test_noise_does_not_change_block_fingerprints(self):
        a = snapshot({"html": PAGE})
        b = snapshot({"html": noisy_variant(PAGE)})
        self.assertEqual([x["fingerprint"] for x in a["blocks"]],
                         [x["fingerprint"] for x in b["blocks"]])

    def test_noise_does_not_trigger_diff(self):
        a = snapshot({"html": PAGE})
        b = snapshot({"html": noisy_variant(PAGE)})
        self.assertFalse(diff(a, b)["changed"])

    def test_raw_hash_still_tracks_page(self):
        # 原始页哈希仍随任何字节变化（兼容旧口径）
        self.assertNotEqual(snapshot({"html": PAGE})["hash"],
                            snapshot({"html": noisy_variant(PAGE)})["hash"])


class BlockLocateTest(unittest.TestCase):
    def test_modified_block_located(self):
        a = snapshot({"html": PAGE})
        b = snapshot({"html": PAGE.replace("天气预报，晴", "天气预报，晴转多云")})
        result = diff(a, b)
        self.assertTrue(result["changed"])
        self.assertEqual(len(result["blocks"]), 1)
        change = result["blocks"][0]
        self.assertEqual(change["change"], "modified")
        self.assertEqual(change["id"], "p#2")
        self.assertIn("晴", change["before"])
        self.assertIn("晴转多云", change["after"])
        self.assertIn("晴转多云", change["sample_after"])  # 块级样本供复核
        self.assertIn("p#2", result["summary"])

    def test_added_and_removed_blocks_located(self):
        a = snapshot({"html": PAGE})
        b = snapshot({"html": PAGE.replace("<p>第一条：要闻正文。</p>", "")
                                 .replace("</body>", "<p>第三条：快讯。</p></body>")})
        result = diff(a, b)
        kinds = {(c["id"], c["change"]) for c in result["blocks"]}
        # 第二条指纹未变被配对吸收；第一条删除、新第三条（序号为 p#2）新增
        self.assertEqual(kinds, {("p#1", "removed"), ("p#2", "added")})

    def test_diff_reports_version(self):
        a = snapshot({"html": PAGE})
        b = snapshot({"html": PAGE.replace("晴", "多云"), "version": 5})
        self.assertEqual(diff(a, b)["version"], 5)


class VersionStoreTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="snapdiff-test-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.store = SnapshotStore(self.root, retention_days=7)

    def _snap(self, html, ts):
        return snapshot({"site": "news.example", "html": html, "ts": ts}, store=self.store)

    def test_versions_kept_and_fetched_by_time(self):
        self._snap(PAGE, 1000)
        self._snap(noisy_variant(PAGE), 2000)
        self._snap(PAGE.replace("晴", "多云"), 3000)
        self.assertEqual(self.store.versions("news.example"), [1, 2, 3])
        self.assertEqual(self.store.get_at("news.example", 2500)["version"], 2)
        self.assertIsNone(self.store.get_at("news.example", 500))
        self.assertEqual(self.store.get("news.example", 3)["blocks"][0]["tag"], "h1")

    def test_cleanup_by_retention_keeps_latest(self):
        self._snap(PAGE, 1000)
        self._snap(PAGE.replace("晴", "多云"), 2000)
        removed = self.store.cleanup(now=1000 + 8 * 86400)  # 两版都过 7 天保留期
        self.assertEqual(removed, 1)
        self.assertEqual(self.store.versions("news.example"), [2])  # 最新版保留作基线

    def test_changes_queryable_by_site_and_time(self):
        a = self._snap(PAGE, 1000)
        b = self._snap(PAGE.replace("晴", "多云"), 2000)
        diff(a, b, store=self.store)
        records = self.store.query_changes("news.example", start=1500, end=2500)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["to_version"], 2)
        self.assertEqual(records[0]["blocks"][0]["id"], "p#2")  # 块级样本随记录保留
        self.assertEqual(self.store.query_changes("news.example", start=3000), [])
        self.assertEqual(self.store.query_changes("other.example"), [])


class FalsePositiveTest(unittest.TestCase):
    def test_marked_sample_is_suppressed(self):
        before = snapshot({"html": "<p>秒杀倒计时 10 天</p>"})
        after = snapshot({"html": "<p>秒杀倒计时 9 天</p>"})
        self.assertTrue(diff(before, after)["changed"])
        mark_false_positive(before["blocks"][0]["fingerprint"],
                            after["blocks"][0]["fingerprint"])
        result = diff(before, after)
        self.assertFalse(result["changed"])
        self.assertEqual(result["blocks"], [])

    def test_store_marks_persist_and_reload(self):
        root = tempfile.mkdtemp(prefix="snapdiff-test-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        before = snapshot({"html": "<p>已售 100 件</p>"})
        after = snapshot({"html": "<p>已售 101 件</p>"})
        store = SnapshotStore(root)
        store.mark_false_positive("shop.example",
                                  before["blocks"][0]["fingerprint"],
                                  after["blocks"][0]["fingerprint"],
                                  note="销量计数，运营确认误报")
        self.assertFalse(diff(before, after)["changed"])
        SnapshotStore(root)  # 重新打开时误报样本从磁盘恢复
        self.assertFalse(diff(before, after)["changed"])


if __name__ == "__main__":
    unittest.main()
