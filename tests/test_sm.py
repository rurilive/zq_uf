import unittest

from sitemapinc2 import (confirm, diff, history, merge_shard, purge, reset,
                         rollback, snapshot, verify)


class SmTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_newer_lastmod_wins(self):
        merge_shard("s0", 1, {"a": 1})
        r = merge_shard("s1", 1, {"a": 2})
        self.assertEqual(r["updated"], 1)
        self.assertEqual(snapshot(), {"a": 2})

    def test_older_lastmod_skipped(self):
        merge_shard("s0", 1, {"a": 5})
        r = merge_shard("s1", 1, {"a": 1})
        self.assertEqual(r["skipped"], 1)
        self.assertEqual(snapshot(), {"a": 5})

    def test_upstream_delete_leaves_tombstone(self):
        merge_shard("s0", 1, {"a": 1, "b": 1}, now=100)
        r = merge_shard("s0", 2, {"a": 1}, now=200)
        self.assertEqual(r["deleted"], 1)
        self.assertEqual(snapshot(), {"a": 1})
        self.assertEqual(diff()["deleted"], ["b"])

    def test_delete_only_own_shard(self):
        merge_shard("s0", 1, {"a": 1})
        merge_shard("s1", 1, {"a": 9})  # s1 用更新的 lastmod 抢走 a
        r = merge_shard("s0", 2, {})    # s0 删 a 不应墓碑 s1 的版本
        self.assertEqual(r["deleted"], 0)
        self.assertEqual(snapshot(), {"a": 9})

    def test_same_seq_idempotent(self):
        merge_shard("s0", 1, {"a": 1})
        r = merge_shard("s0", 1, {"a": 1})
        self.assertTrue(r["idempotent"])
        self.assertEqual((r["added"], r["updated"], r["deleted"]), (0, 0, 0))
        self.assertEqual(len(history()), 1)  # 不产生第二轮

    def test_stale_seq_skipped(self):
        merge_shard("s0", 2, {"a": 2})
        r = merge_shard("s0", 1, {"a": 1, "b": 1})
        self.assertTrue(r["stale"])
        self.assertEqual(snapshot(), {"a": 2})

    def test_readded_url_clears_tombstone(self):
        merge_shard("s0", 1, {"a": 1}, now=100)
        merge_shard("s0", 2, {}, now=200)
        r = merge_shard("s0", 3, {"a": 3}, now=300)
        self.assertEqual(r["added"], 1)
        self.assertEqual(rollback(since=0)["restored"], 0)

    def test_diff_by_round(self):
        merge_shard("s0", 1, {"a": 1}, now=100)
        merge_shard("s0", 2, {"b": 1}, now=200)
        self.assertEqual(diff(1)["added"], ["a"])
        self.assertEqual(diff(2)["added"], ["b"])
        self.assertEqual([h["id"] for h in history()], [1, 2])

    def test_rollback_restores_recent_deletes(self):
        merge_shard("s0", 1, {"a": 1, "b": 1}, now=100)
        merge_shard("s0", 2, {}, now=200)
        r = rollback(since=150)
        self.assertEqual(r["restored"], 2)
        self.assertEqual(snapshot(), {"a": 1, "b": 1})

    def test_purge_cleans_old_tombstones(self):
        merge_shard("s0", 1, {"a": 1}, now=100)
        merge_shard("s0", 2, {}, now=200)
        self.assertEqual(purge(before=300)["purged"], 1)
        self.assertEqual(rollback(since=0)["restored"], 0)

    def test_shard_failure_isolated(self):
        merge_shard("s0", 1, {"a": 1})
        with self.assertRaises(TypeError):
            merge_shard("s1", 1, "not-a-dict")
        r = merge_shard("s2", 1, {"b": 2})  # 其他分片不受影响
        self.assertEqual(r["added"], 1)
        r = merge_shard("s1", 1, {"c": 3})  # 失败分片可同序号重下
        self.assertEqual(r["added"], 1)

    def test_verify_ok(self):
        merge_shard("s0", 1, {"a": 1})
        r = verify({"a": 1})
        self.assertEqual(r["status"], "ok")
        self.assertFalse(r["halted"])

    def test_verify_exceeds_halts_until_confirmed(self):
        merge_shard("s0", 1, {"a": 1})
        r = verify({"a": 1, "b": 2}, max_diff=0)
        self.assertEqual(r["status"], "needs_review")
        self.assertEqual(r["missing"], ["b"])
        with self.assertRaises(RuntimeError):
            merge_shard("s1", 1, {"c": 3})
        confirm()
        self.assertEqual(merge_shard("s1", 1, {"c": 3})["added"], 1)


if __name__ == "__main__":
    unittest.main()
