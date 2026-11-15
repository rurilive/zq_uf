import hashlib
import time
import unittest

from pagetrack import (changes, pull, reset, rollback, save, subscribe,
                       versions)


class TrackTest(unittest.TestCase):
    def test_save_has_no_version(self):
        self.assertIsNone(save("https://a/1", "<p>a</p>")["version"])

    def test_save_has_no_fingerprint(self):
        self.assertIsNone(save("https://a/2", "<p>b</p>")["fingerprint"])

    def test_changes_empty(self):
        save("https://a/1", "<p>a</p>")
        self.assertEqual(changes(0)["changes"], [])


class VersionedTrackTest(unittest.TestCase):
    def setUp(self):
        reset()

    def tearDown(self):
        reset()

    def test_version_numbers_increment(self):
        first = save("https://s/1", "<p>1</p>")
        self.assertEqual(first["version_no"], 1)
        second = save("https://s/1", "<p>2</p>")
        self.assertEqual(second["version_no"], 2)
        self.assertEqual(second["version"], 1)  # 被取代的旧版本
        third = save("https://s/1", "<p>3</p>")
        self.assertEqual(third["version_no"], 3)

    def test_fingerprint_is_sha256(self):
        receipt = save("https://s/1", "<p>1</p>")
        self.assertEqual(receipt["digest"],
                         hashlib.sha256(b"<p>1</p>").hexdigest())

    def test_duplicate_save_no_new_version(self):
        save("https://s/1", "<p>1</p>")
        again = save("https://s/1", "<p>1</p>")
        self.assertFalse(again["created"])
        self.assertIsNone(again["seq"])
        self.assertEqual(len(versions("https://s/1")["versions"]), 1)
        self.assertEqual(changes(0)["changes"], [])

    def test_changes_have_sequence_and_increment(self):
        save("https://s/1", "<p>1</p>")
        save("https://s/1", "<p>2</p>")
        save("https://s/2", "<p>x</p>")
        save("https://s/2", "<p>y</p>")
        entries = changes(0)["changes"]
        self.assertEqual([e["seq"] for e in entries], [1, 2])
        # 增量采集：从序号 1 继续
        rest = changes(1)
        self.assertEqual([e["seq"] for e in rest["changes"]], [2])
        self.assertEqual(rest["cursor"], 2)

    def test_query_by_site_and_time(self):
        save("https://shop/1", "<p>1</p>")
        save("https://shop/1", "<p>2</p>")
        mid = time.time()
        save("https://blog/1", "<p>b</p>")
        save("https://blog/1", "<p>c</p>")
        shop = changes(0, site="shop")["changes"]
        self.assertEqual([e["page"] for e in shop], ["https://shop/1"])
        recent = changes(0, start=mid)["changes"]
        self.assertEqual([e["site"] for e in recent], ["blog"])

    def test_subscribe_pull_and_resume(self):
        save("https://s/1", "<p>1</p>")
        sub = subscribe()["subscription"]
        save("https://s/1", "<p>2</p>")
        save("https://s/1", "<p>3</p>")
        first = pull(sub)
        self.assertEqual([e["version"] for e in first["changes"]], [2, 3])
        # 断线后续拉：游标保留，不重复、不丢
        self.assertEqual(pull(sub)["changes"], [])
        save("https://s/1", "<p>4</p>")
        resumed = pull(sub)
        self.assertEqual([e["version"] for e in resumed["changes"]], [4])

    def test_subscribe_filters_by_site(self):
        save("https://shop/1", "<p>1</p>")
        sub = subscribe(site="shop")["subscription"]
        save("https://blog/1", "<p>b</p>")
        save("https://blog/1", "<p>c</p>")
        save("https://shop/1", "<p>2</p>")
        entries = pull(sub)["changes"]
        self.assertEqual([e["site"] for e in entries], ["shop"])

    def test_rollback_keeps_history(self):
        save("https://s/1", "<p>1</p>")
        save("https://s/1", "<p>2</p>")
        receipt = rollback("https://s/1", 1)
        self.assertTrue(receipt["created"])
        history = versions("https://s/1")["versions"]
        self.assertEqual(len(history), 3)
        self.assertEqual(history[-1]["content"], "<p>1</p>")
        self.assertEqual(changes(0)["changes"][-1]["kind"], "rollback")


if __name__ == "__main__":
    unittest.main()
