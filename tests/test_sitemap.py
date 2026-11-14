import unittest

from sitemapinc import diff, merge
from sitemapinc import sitemap
from sitemapinc import (
    fetch_shards,
    merge_shards,
    report,
    rollback_last,
    tombstones,
)


class SitemapTest(unittest.TestCase):
    def setUp(self):
        sitemap._index.clear()

    def test_merge_keeps_new(self):
        self.assertEqual(merge({"a": 1}, {"b": 2})["b"], 2)

    def test_merge_overwrites(self):
        self.assertEqual(merge({"a": 1}, {"a": 2})["a"], 2)

    def test_diff_shape(self):
        self.assertEqual(set(diff()), {"added", "removed", "updated"})


def entry(lastmod, body="x"):
    return {"loc": "https://example.com/", "lastmod": lastmod, "body": body}


class IncrementalTest(unittest.TestCase):
    """后面几组用例共用的隔离与夹具；不含断言，避免基类用例被重复执行。"""

    def setUp(self):
        sitemap._index.clear()

    def index(self):
        return dict(sitemap._index)


class LastmodTest(IncrementalTest):
    def test_newer_lastmod_overwrites(self):
        old = {"u": entry("2024-01-01T00:00:00Z", "old")}
        new = {"u": entry("2024-02-01T00:00:00Z", "new")}
        merged = merge(old, new)
        self.assertEqual(merged["u"]["body"], "new")
        self.assertEqual(diff()["updated"], ["u"])
        self.assertEqual(report()["added"], 0)
        self.assertEqual(report()["skipped"], 0)

    def test_older_lastmod_is_skipped(self):
        old = {"u": entry("2024-03-01T00:00:00Z", "current")}
        new = {"u": entry("2024-02-01T00:00:00Z", "stale")}
        merged = merge(old, new)
        self.assertEqual(merged["u"]["body"], "current")
        self.assertEqual(diff()["updated"], [])
        self.assertEqual(report()["skipped"], 1)
        self.assertNotIn("u", tombstones())

    def test_equal_lastmod_is_idempotent(self):
        old = {"u": entry("2024-03-01T00:00:00Z", "current")}
        new = {"u": entry("2024-03-01T00:00:00Z", "reworded")}
        merged = merge(old, new)
        self.assertEqual(merged["u"]["body"], "current")
        self.assertEqual(report()["updated"], 0)
        self.assertEqual(report()["skipped"], 1)

    def test_timezone_offsets_are_compared(self):
        old = {"u": entry("2024-03-01T00:00:00+08:00", "old")}
        new = {"u": entry("2024-02-29T20:00:00Z", "new")}
        # 2024-03-01T00:00+08:00 == 2024-02-29T16:00Z，新值晚 4 小时
        self.assertEqual(merge(old, new)["u"]["body"], "new")

    def test_plain_lastmod_value(self):
        merged = merge({"u": "2024-01-01"}, {"u": "2024-05-01"})
        self.assertEqual(merged["u"], "2024-05-01")

    def test_missing_lastmod_keeps_legacy_overwrite(self):
        merged = merge({"u": 1}, {"u": 2})
        self.assertEqual(merged["u"], 2)

    def test_replayed_snapshot_is_a_noop(self):
        old = {"a": entry("2024-01-01"), "b": entry("2024-02-01")}
        merge(old, old)
        self.assertEqual(self.index(), old)
        self.assertEqual(
            report(),
            {"added": 0, "updated": 0, "removed": 0, "skipped": 2, "tombstones": 0},
        )


class DeletionTest(IncrementalTest):
    def test_missing_url_is_removed_and_tombstoned(self):
        old = {"a": entry("2024-01-01"), "b": entry("2024-01-01")}
        new = {"a": entry("2024-01-01")}
        merged = merge(old, new)
        self.assertNotIn("b", merged)
        self.assertEqual(diff()["removed"], ["b"])
        self.assertIn("b", tombstones())
        self.assertEqual(tombstones()["b"]["value"], old["b"])
        self.assertEqual(tombstones()["b"]["lastmod"], "2024-01-01")
        self.assertEqual(report()["removed"], 1)
        self.assertEqual(report()["tombstones"], 1)

    def test_tombstone_keeps_latest_generation_only(self):
        merge({"u": entry("2024-01-01", "v1")}, {})
        self.assertEqual(tombstones()["u"]["value"]["body"], "v1")
        # 复活后再以更新一代删除，墓碑只保留最近一代
        merge(self.index(), {"u": entry("2024-05-01", "v2")})
        merge(self.index(), {})
        self.assertEqual(tombstones()["u"]["value"]["body"], "v2")
        self.assertEqual(list(tombstones()), ["u"])

    def test_resurrected_url_drops_tombstone(self):
        merge({"a": entry("2024-01-01"), "b": entry("2024-01-01")}, {"a": entry("2024-01-01")})
        self.assertIn("b", tombstones())
        merge(self.index(), {"a": entry("2024-01-01"), "b": entry("2024-02-01")})
        self.assertNotIn("b", tombstones())
        self.assertIn("b", diff()["added"])

    def test_incomplete_merge_does_not_delete(self):
        old = {"a": entry("2024-01-01"), "b": entry("2024-01-01")}
        merged = merge(old, {"a": entry("2024-01-01")}, complete=False)
        self.assertIn("b", merged)
        self.assertEqual(diff()["removed"], [])
        self.assertEqual(tombstones(), {})

    def test_rollback_restores_last_generation(self):
        old = {"a": entry("2024-01-01"), "b": entry("2024-01-01")}
        merge(old, {"a": entry("2024-01-01")})
        self.assertEqual(rollback_last(), ["b"])
        self.assertEqual(self.index()["b"], old["b"])
        self.assertEqual(tombstones(), {})
        self.assertEqual(rollback_last(), [])

    def test_rollback_skips_merges_that_deleted_nothing(self):
        merge({"a": entry("2024-01-01"), "b": entry("2024-01-01")}, {"a": entry("2024-01-01")})
        # 这次没有删除，回滚它不应把上一代的墓碑复活
        merge(self.index(), {"a": entry("2024-01-01")})
        self.assertEqual(rollback_last(), [])
        self.assertIn("b", tombstones())
        self.assertNotIn("b", self.index())


class ShardTest(IncrementalTest):
    def shards(self):
        return [
            {"a": entry("2024-01-01"), "b": entry("2024-01-01")},
            {"c": entry("2024-01-01"), "d": entry("2024-01-01")},
        ]

    def test_shard_merge_unions(self):
        merged = merge_shards({}, self.shards())
        self.assertEqual(set(merged), {"a", "b", "c", "d"})
        self.assertEqual(report()["added"], 4)

    def test_merge_accepts_shard_list(self):
        self.assertEqual(set(merge({}, self.shards())), {"a", "b", "c", "d"})

    def test_merge_accepts_fetch_result(self):
        def fetch(shard):
            if "d" in shard:
                raise IOError("boom")
            return shard

        old = merge_shards({}, self.shards() + [{"e": entry("2024-01-01")}])
        result = fetch_shards(self.shards(), fetch)
        merged = merge(old, result)
        # 失败片没下到，不能把 d、e 当成被删除
        self.assertEqual(set(merged), {"a", "b", "c", "d", "e"})
        self.assertEqual(tombstones(), {})

    def test_duplicate_shards_are_idempotent(self):
        first = merge_shards({}, self.shards())
        second = merge_shards(first, self.shards() + self.shards())
        self.assertEqual(first, second)
        self.assertEqual(report()["added"], 0)
        self.assertEqual(report()["updated"], 0)
        self.assertEqual(report()["skipped"], 4)

    def test_shards_resolve_conflicts_by_lastmod(self):
        shards = [
            {"u": entry("2024-01-01", "old")},
            {"u": entry("2024-02-01", "new")},
        ]
        self.assertEqual(merge_shards({}, shards)["u"]["body"], "new")
        self.assertEqual(merge_shards({}, list(reversed(shards)))["u"]["body"], "new")

    def test_complete_shard_set_propagates_deletion(self):
        old = merge_shards({}, self.shards())
        shrunk = [self.shards()[0]]
        merged = merge_shards(old, shrunk)
        self.assertEqual(set(merged), {"a", "b"})
        self.assertEqual(sorted(tombstones()), ["c", "d"])
        self.assertEqual(report()["tombstones"], 2)

    def test_partial_fetch_keeps_undownloaded_urls(self):
        old = merge_shards({}, self.shards())

        def fetch(shard):
            if "d" in shard:
                raise IOError("boom")
            return shard

        result = fetch_shards(self.shards(), fetch)
        self.assertFalse(result.complete)
        merged = result.merge_into(old)
        self.assertIn("d", merged)
        self.assertNotIn("d", tombstones())
        self.assertEqual(report()["removed"], 0)

    def test_failed_shard_is_refetched_alone(self):
        calls = []

        def fetch(shard):
            calls.append(frozenset(shard))
            if "c" in shard and calls.count(frozenset(shard)) == 1:
                raise IOError("boom")
            return shard

        result = fetch_shards(self.shards(), fetch, retries=1)
        self.assertTrue(result.complete)
        self.assertEqual(result.attempts, {0: 1, 1: 2})
        # 成功片只下载一次，失败片只重取自己
        self.assertEqual(calls, [frozenset("ab"), frozenset("cd"), frozenset("cd")])
        self.assertEqual(set(result.merge_into({})), {"a", "b", "c", "d"})

    def test_all_shards_failing_keeps_index(self):
        old = merge_shards({}, self.shards())

        def fetch(_shard):
            raise IOError("down")

        result = fetch_shards(self.shards(), fetch)
        self.assertEqual(set(result.failed), {0, 1})
        self.assertEqual(result.merge_into(old), old)
        self.assertEqual(tombstones(), {})


if __name__ == "__main__":
    unittest.main()
