"""聚合版审计测试：聚合、去重、敏感标记、游标、回放、归档、操作人变更。"""

import unittest

from tableaudit import (
    archive_expired,
    configure,
    drill_down,
    fingerprint,
    operator_changes,
    query,
    read_archive,
    record,
    replay,
    reset,
)

HOUR = 3600
DAY = 86400
T0 = 1_700_000_000  # 固定基准时间，保证桶稳定


def evt(table, op, user="alice", ts=T0, **extra):
    data = {"table": table, "op": op, "user": user, "time": ts}
    data.update(extra)
    return data


class AggregateTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_aggregated_by_table_user_op_in_hour_window(self):
        record(evt("orders", "select", "etl", T0))
        record(evt("orders", "select", "etl", T0 + 60))
        record(evt("orders", "select", "etl", T0 + 120))
        res = query({"table": "orders"})
        self.assertTrue(res["aggregated"])
        self.assertEqual(len(res["hits"]), 1)
        agg = res["hits"][0]
        self.assertEqual(agg["count"], 3)
        self.assertEqual(agg["table"], "orders")
        self.assertEqual(agg["user"], "etl")
        self.assertEqual(agg["op"], "select")
        self.assertEqual(agg["granularity"], "hour")

    def test_hour_and_day_two_level_windows(self):
        record(evt("t", "select", "u", T0))
        hour_hits = query({"table": "t"}, )["hits"]
        day_hits = query({"table": "t", "granularity": "day"})["hits"]
        self.assertEqual(len(hour_hits), 1)
        self.assertEqual(len(day_hits), 1)
        self.assertEqual(hour_hits[0]["window_end"] -
                         hour_hits[0]["window_start"], HOUR)
        self.assertEqual(day_hits[0]["window_end"] -
                         day_hits[0]["window_start"], DAY)

    def test_different_buckets_split_aggregates(self):
        record(evt("t", "select", "u", T0))
        record(evt("t", "select", "u", T0 + HOUR))
        hits = query({"table": "t"})["hits"]
        self.assertEqual(len(hits), 2)
        self.assertEqual(sum(h["count"] for h in hits), 2)

    def test_different_keys_split_aggregates(self):
        record(evt("t", "select", "u1", T0))
        record(evt("t", "select", "u2", T0))
        record(evt("t", "insert", "u1", T0))
        hits = query({"table": "t"})["hits"]
        self.assertEqual(len(hits), 3)


class DedupTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_fingerprint_ignores_time_fields(self):
        fp1 = fingerprint(evt("t", "select", "u", T0))
        fp2 = fingerprint(evt("t", "select", "u", T0 + 999))
        self.assertEqual(fp1, fp2)

    def test_fingerprint_differs_on_payload(self):
        fp1 = fingerprint(evt("t", "select", "u", T0, rows=1))
        fp2 = fingerprint(evt("t", "select", "u", T0, rows=2))
        self.assertNotEqual(fp1, fp2)

    def test_duplicates_counted_within_window(self):
        r1 = record(evt("t", "select", "u", T0, rows=1))
        r2 = record(evt("t", "select", "u", T0 + 10, rows=1))
        r3 = record(evt("t", "select", "u", T0 + 20, rows=2))
        self.assertFalse(r1["duplicate"])
        self.assertTrue(r2["duplicate"])
        self.assertFalse(r3["duplicate"])
        # total 仍按原始事件增长
        self.assertEqual(r3["total"], 3)
        agg = query({"table": "t"})["hits"][0]
        self.assertEqual(agg["count"], 3)
        self.assertEqual(agg["unique"], 2)
        self.assertEqual(agg["duplicates"], 1)

    def test_dedup_scoped_to_window_and_key(self):
        # 同指纹跨小时桶不去重
        record(evt("t", "select", "u", T0, rows=1))
        record(evt("t", "select", "u", T0 + HOUR, rows=1))
        for agg in query({"table": "t"})["hits"]:
            self.assertEqual(agg["unique"], 1)
            self.assertEqual(agg["duplicates"], 0)


class SensitiveTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_sensitive_marked(self):
        record(evt("t", "drop", "dba", T0))
        agg = query({"table": "t"})["hits"][0]
        self.assertTrue(agg["sensitive"])

    def test_sensitive_raw_retained_in_full(self):
        configure(sample_limit=2)
        for i in range(5):
            record(evt("t", "truncate", "dba", T0 + i, seq=i))
        agg = query({"table": "t"}, include_raw=True)["hits"][0]
        self.assertTrue(agg["sensitive"])
        self.assertEqual(agg["raw_count"], 5)
        # 原文单独全量保留，不受样本上限影响
        self.assertEqual(len(agg["raw"]), 5)
        # 普通样本仍受上限约束
        self.assertLessEqual(len(agg["samples"]), 2)

    def test_sensitive_configurable(self):
        configure(sensitive_ops={"select"})
        record(evt("t", "select", "u", T0))
        self.assertTrue(query({"table": "t"})["hits"][0]["sensitive"])
        reset()
        record(evt("t", "drop", "u", T0))
        self.assertTrue(query({"table": "t"})["hits"][0]["sensitive"])

    def test_non_sensitive_keeps_samples_only(self):
        record(evt("t", "select", "u", T0, seq=1))
        record(evt("t", "select", "u", T0 + 1, seq=2))
        agg = query({"table": "t"}, include_raw=True)["hits"][0]
        self.assertFalse(agg["sensitive"])
        self.assertIsNone(agg["raw"])
        self.assertEqual(len(agg["samples"]), 2)


class QueryTest(unittest.TestCase):
    def setUp(self):
        reset()
        # 两个表、两个操作人、跨两个小时
        record(evt("orders", "select", "alice", T0))
        record(evt("orders", "drop", "bob", T0 + 5))
        record(evt("pay", "select", "alice", T0 + HOUR))
        record(evt("orders", "select", "alice", T0 + HOUR + 1))

    def test_exact_filters(self):
        self.assertEqual(len(query({"table": "orders"})["hits"]), 3)
        self.assertEqual(len(query({"user": "bob"})["hits"]), 1)
        self.assertEqual(len(query({"op": "drop"})["hits"]), 1)
        self.assertEqual(len(query({"table": "orders", "user": "alice"})
                              ["hits"]), 2)

    def test_results_ordered_by_time_desc(self):
        hits = query({"table": "orders"})["hits"]
        seen = [h["last_seen"] for h in hits]
        self.assertEqual(seen, sorted(seen, reverse=True))

    def test_time_range_filter(self):
        hits = query({"table": "orders", "start": T0 + HOUR})["hits"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["op"], "select")

    def test_cursor_pagination(self):
        page1 = query({"table": "orders"}, limit=2)
        self.assertEqual(len(page1["hits"]), 2)
        self.assertTrue(page1["has_more"])
        self.assertIsNotNone(page1["cursor"])
        page2 = query({"table": "orders"}, limit=2,
                      cursor=page1["cursor"])
        self.assertEqual(len(page2["hits"]), 1)
        self.assertFalse(page2["has_more"])
        self.assertIsNone(page2["cursor"])
        # 两页不重不漏
        keys1 = {(h["bucket"], h["user"], h["op"]) for h in page1["hits"]}
        keys2 = {(h["bucket"], h["user"], h["op"]) for h in page2["hits"]}
        self.assertTrue(keys1.isdisjoint(keys2))

    def test_drill_down_by_table_and_day(self):
        hour_hits = drill_down("orders", day=T0)
        self.assertTrue(all(h["granularity"] == "hour"
                            and h["table"] == "orders" for h in hour_hits))
        self.assertEqual(len(hour_hits), 3)
        # 限定操作人下钻
        only_bob = drill_down("orders", day=T0, user="bob")
        self.assertEqual(len(only_bob), 1)
        self.assertEqual(only_bob[0]["op"], "drop")


class OperatorChangeTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_operator_change_recorded(self):
        record(evt("t", "drop", "alice", T0))
        r = record(evt("t", "drop", "bob", T0 + 10))
        change = r["operator_change"]
        self.assertIsNotNone(change)
        self.assertEqual(change["from_user"], "alice")
        self.assertEqual(change["to_user"], "bob")
        changes = operator_changes(table="t")
        self.assertEqual(len(changes), 1)

    def test_same_user_no_change(self):
        record(evt("t", "drop", "alice", T0))
        r = record(evt("t", "drop", "alice", T0 + 10))
        self.assertIsNone(r["operator_change"])
        self.assertEqual(operator_changes(), [])


class RetentionArchiveTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_expired_data_archived(self):
        configure(retention_seconds=2 * HOUR)
        record(evt("old", "select", "u", T0))
        record(evt("old", "drop", "u", T0 + 5))
        record(evt("new", "select", "u", T0 + 3 * HOUR))
        archived = archive_expired(now=T0 + 3 * HOUR)
        # 旧桶的小时聚合（select + drop）全部过期
        self.assertTrue(archived)
        archived_tables = {a["data"]["table"] for a in archived
                           if a["kind"] == "aggregate"}
        self.assertIn("old", archived_tables)
        self.assertNotIn("new", archived_tables)
        # 过期数据查不到，未过期的还在
        self.assertEqual(query({"table": "old"})["hits"], [])
        self.assertEqual(len(query({"table": "new"})["hits"]), 1)
        # 归档条目可查，敏感原文随归档保留
        entries = read_archive()
        sensitive = [a for a in entries
                     if a["kind"] == "aggregate" and a["data"]["sensitive"]]
        self.assertTrue(sensitive)
        self.assertEqual(sensitive[0]["data"]["op"], "drop")
        self.assertEqual(len(sensitive[0]["raw"]), 1)

    def test_no_retention_means_keep_forever(self):
        record(evt("t", "select", "u", T0))
        self.assertEqual(archive_expired(now=T0 + 400 * DAY), [])
        self.assertEqual(len(query({"table": "t"})["hits"]), 1)

    def test_archive_written_to_dir(self):
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            configure(retention_seconds=HOUR, archive_dir=tmp)
            record(evt("t", "select", "u", T0))
            archive_expired(now=T0 + 2 * HOUR)
            path = os.path.join(tmp, "audit-archive.jsonl")
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as fh:
                lines = [line for line in fh.read().splitlines() if line]
            self.assertTrue(lines)


class ReplayTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_replay_order_and_retention_mode(self):
        record(evt("t", "select", "alice", T0 + 10, seq=1))
        record(evt("t", "select", "alice", T0 + 5, seq=2))
        record(evt("t", "drop", "alice", T0 + 15, seq=3))
        record(evt("t", "drop", "bob", T0 + 20, seq=4))
        record(evt("t", "drop", "bob", T0 + 25, seq=5))
        entries = replay({"table": "t"})
        kinds = [(e["kind"], e.get("retained")) for e in entries]
        # 普通 select 两条不同指纹 -> sample；敏感 drop 三条 -> full
        self.assertIn(("event", "sample"), kinds)
        self.assertEqual(kinds.count(("event", "full")), 3)
        # 操作人变更并入回放
        self.assertIn("operator_change", [e["kind"] for e in entries])
        # 时间正序
        times = [
            e["event"]["time"] if e["kind"] == "event"
            else e["change"]["time"]
            for e in entries
        ]
        self.assertEqual(times, sorted(times))

    def test_replay_filtered(self):
        record(evt("a", "select", "u", T0))
        record(evt("b", "select", "u", T0))
        entries = replay({"table": "a"})
        self.assertTrue(all(
            e["kind"] != "event" or e["event"]["table"] == "a"
            for e in entries
        ))


if __name__ == "__main__":
    unittest.main()
