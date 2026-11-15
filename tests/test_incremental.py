import unittest

from sitemapinc2 import configure, diff, merge, reset, snapshot


class IncrementalTest(unittest.TestCase):
    def setUp(self):
        reset()

    def tearDown(self):
        reset()

    # ---- lastmod 比较 ----

    def test_lastmod_newer_updates(self):
        merge({}, {"u": 1}, now=10)
        r = merge({"u": 1}, {"u": 2}, now=20)
        self.assertEqual(r["updated"], 1)
        self.assertEqual(snapshot()["u"], 2.0)

    def test_lastmod_older_skipped(self):
        merge({}, {"u": 5}, now=10)
        r = merge({"u": 5}, {"u": 3}, now=20)
        self.assertEqual(r["updated"], 0)
        self.assertEqual(r["skipped"], 1)
        self.assertEqual(snapshot()["u"], 5.0)

    def test_lastmod_equal_skipped(self):
        merge({}, {"u": 1}, now=10)
        r = merge({"u": 1}, {"u": 1}, now=20)
        self.assertEqual(r["skipped"], 1)
        self.assertEqual(r["updated"], 0)

    def test_lastmod_iso_string(self):
        merge({}, {"u": "2024-01-01T00:00:00Z"}, now=10)
        r = merge({"u": "2024-01-01T00:00:00Z"},
                  {"u": "2024-06-01T00:00:00Z"}, now=20)
        self.assertEqual(r["updated"], 1)

    # ---- 删除传播与墓碑 ----

    def test_delete_leaves_tombstone(self):
        merge({}, {"u": 1}, now=10)
        r = merge({"u": 1}, {}, now=20)
        self.assertEqual(r["deleted"], 1)
        self.assertEqual(r["tombstones"], 1)
        self.assertEqual(r["total"], 0)
        self.assertNotIn("u", snapshot())
        self.assertEqual(diff()["deleted"], ["u"])

    def test_delete_idempotent(self):
        merge({}, {"u": 1}, now=10)
        merge({"u": 1}, {}, now=20)
        r = merge({"u": 1}, {}, now=30)
        self.assertEqual(r["deleted"], 0)
        self.assertEqual(r["skipped"], 1)
        self.assertEqual(r["tombstones"], 1)

    def test_revive_with_newer_lastmod(self):
        merge({}, {"u": 1}, now=10)
        merge({"u": 1}, {}, now=20)
        r = merge({}, {"u": 2}, now=30)
        self.assertEqual(r["added"], 1)
        self.assertEqual(r["tombstones"], 0)
        self.assertEqual(snapshot()["u"], 2.0)

    def test_stale_revive_blocked(self):
        merge({}, {"u": 5}, now=10)
        merge({"u": 5}, {}, now=20)
        r = merge({}, {"u": 3}, now=30)
        self.assertEqual(r["added"], 0)
        self.assertEqual(r["skipped"], 1)
        self.assertEqual(r["tombstones"], 1)

    def test_tombstone_expiry_configurable(self):
        configure(tombstone_ttl=100)
        merge({}, {"u": 1}, now=1000)
        merge({"u": 1}, {}, now=1000)
        r = merge({}, {}, now=1099)
        self.assertEqual(r["expired"], 0)
        self.assertEqual(r["tombstones"], 1)
        r = merge({}, {}, now=1100)
        self.assertEqual(r["expired"], 1)
        self.assertEqual(r["tombstones"], 0)

    def test_tombstone_ttl_per_call(self):
        merge({}, {"u": 1}, now=1000)
        merge({"u": 1}, {}, now=1000)
        r = merge({}, {}, now=1005, tombstone_ttl=4)
        self.assertEqual(r["expired"], 1)

    # ---- 分片合并 ----

    def test_shard_out_of_order_replayed_by_seq(self):
        merge({}, {"b": 1}, shard_seq=2, now=10)
        r = merge({}, {"a": 1}, shard_seq=1, now=20)
        self.assertEqual(snapshot(), {"a": 1.0, "b": 1.0})
        self.assertEqual(r["total"], 2)

    def test_shard_duplicate_idempotent(self):
        merge({}, {"a": 1}, shard_seq=1, now=10)
        before = snapshot()
        r = merge({}, {"a": 1}, shard_seq=1, now=20)
        self.assertEqual(r["added"], 0)
        self.assertEqual(r["skipped"], 1)
        self.assertEqual(snapshot(), before)

    def test_shard_deletion_propagates(self):
        merge({}, {"a": 1, "b": 1}, shard_seq=1, now=10)
        r = merge({"a": 1, "b": 1}, {"b": 1}, shard_seq=1, now=20)
        self.assertEqual(r["deleted"], 1)
        self.assertEqual(r["tombstones"], 1)
        self.assertEqual(snapshot(), {"b": 1.0})
        self.assertEqual(diff()["deleted"], ["a"])

    def test_shard_failure_keeps_index(self):
        merge({}, {"a": 1}, shard_seq=1, now=10)
        r = merge(None, None, shard_seq=1, now=20)
        self.assertEqual(r["total"], 1)
        self.assertEqual(snapshot(), {"a": 1.0})

    def test_shard_redownload_replaces_only_that_shard(self):
        merge({}, {"a": 1}, shard_seq=1, now=10)
        merge({}, {"b": 1}, shard_seq=2, now=20)
        r = merge({}, {"a": 2}, shard_seq=1, now=30)
        self.assertEqual(r["updated"], 1)
        self.assertEqual(snapshot(), {"a": 2.0, "b": 1.0})

    # ---- 与全量重建一致 ----

    def test_snapshot_matches_full_rebuild(self):
        merge({}, {"a": 1, "b": 1}, now=10)
        merge({"a": 1, "b": 1}, {"a": 2, "c": 3}, now=20)
        inc = snapshot()
        reset()
        merge({}, {"a": 2, "c": 3}, now=30)
        self.assertEqual(snapshot(), inc)

    def test_shards_match_full_rebuild(self):
        merge({}, {"a": 1, "b": 1}, shard_seq=1, now=10)
        merge({}, {"c": 2}, shard_seq=2, now=20)
        merge({"a": 1, "b": 1}, {"a": 2}, shard_seq=1, now=30)
        inc = snapshot()
        reset()
        merge({}, {"a": 2}, shard_seq=1, now=40)
        merge({}, {"c": 2}, shard_seq=2, now=50)
        self.assertEqual(snapshot(), inc)


if __name__ == "__main__":
    unittest.main()
