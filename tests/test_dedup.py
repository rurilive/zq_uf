import unittest

from rowdedup3 import export, merge, replay, report, reset


class DedupTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_higher_version_wins_regardless_of_arrival(self):
        rows = [
            {"table": "t", "key": "a", "version": 2, "v": 2, "source": "new"},
            {"table": "t", "key": "a", "version": 1, "v": 1, "source": "old"},
        ]
        result = merge(rows)
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["v"], 2)
        self.assertEqual(result["rows"][0]["provenance"]["chosen_source"], "new")
        self.assertEqual(
            [d["reason"] for d in result["dropped_details"]],
            ["older_version"],
        )

    def test_empty_does_not_overwrite_valid(self):
        rows = [
            {"table": "t", "key": "a", "version": 1, "v": 1, "note": "ok", "source": "s1"},
            {"table": "t", "key": "a", "version": 1, "v": 1, "note": None, "source": "s2"},
        ]
        result = merge(rows)
        self.assertEqual(result["rows"][0]["note"], "ok")
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["dropped_details"][0]["reason"], "empty_ignored")

    def test_same_version_completeness_fills_fields(self):
        rows = [
            {"table": "t", "key": "a", "version": 1, "amount": None, "note": "n", "source": "s1"},
            {"table": "t", "key": "a", "version": 1, "amount": 9, "note": "", "source": "s2"},
        ]
        row = merge(rows)["rows"][0]
        self.assertEqual(row["amount"], 9)
        self.assertEqual(row["note"], "n")

    def test_conflict_keeps_both_sides_and_provenance(self):
        rows = [
            {"table": "t", "key": "a", "version": 1, "amount": 10, "source": "s1"},
            {"table": "t", "key": "a", "version": 1, "amount": 20, "source": "s2"},
        ]
        result = merge(rows)
        self.assertEqual(len(result["conflicts"]), 1)
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["field"], "amount")
        self.assertEqual(sorted(s["source"] for s in conflict["sides"]), ["s1", "s2"])
        # 冲突明细在 merge 和 report 中都会保留
        self.assertEqual(report()["conflicts"], result["conflicts"])

    def test_missing_version_filled_from_time(self):
        rows = [
            {"table": "t", "key": "a", "version": 1, "ts": 100, "v": 1, "source": "s1"},
            {"table": "t", "key": "a", "ts": 200, "v": 1, "source": "s2"},
            {"table": "t", "key": "a", "version": 2, "ts": 400, "v": 2, "source": "s3"},
        ]
        result = merge(rows)
        row = result["rows"][0]
        # 无版本记录按时间点之前最近的有效版本补齐，并作为旧版本丢弃
        dropped = {d["source"]: d for d in result["dropped_details"]}
        self.assertEqual(dropped["s2"]["version"], 1)
        self.assertEqual(dropped["s2"]["reason"], "older_version")
        self.assertEqual(row["v"], 2)
        self.assertEqual(row["provenance"]["reason"], "higher_version")
        # 补齐后取值一致，不产生假冲突
        self.assertEqual(result["conflicts"], [])

    def test_missing_version_that_conflicts_keeps_both_sides(self):
        rows = [
            {"table": "t", "key": "a", "version": 1, "ts": 100, "v": 1, "source": "s1"},
            {"table": "t", "key": "a", "ts": 200, "v": 9, "source": "s2"},
        ]
        result = merge(rows)
        # 补齐到版本 1 后与 s1 取值不同：冲突保留两侧，不静默覆盖
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(result["conflicts"][0]["field"], "v")
        self.assertEqual(
            sorted(side["source"] for side in result["conflicts"][0]["sides"]),
            ["s1", "s2"],
        )
        self.assertEqual(result["rows"][0]["provenance"]["reason"], "conflict_kept")

    def test_missing_version_later_than_explicit_version(self):
        rows = [
            {"table": "t", "key": "a", "version": 1, "ts": 100, "v": 1, "source": "s1"},
            {"table": "t", "key": "a", "version": 2, "ts": 200, "v": 2, "source": "s2"},
            {"table": "t", "key": "a", "ts": 300, "v": 2, "source": "s3"},
        ]
        result = merge(rows)
        row = result["rows"][0]
        sources = {s["source"]: s for s in row["provenance"]["sources"]}
        self.assertEqual(sources["s3"]["version"], 2)
        self.assertTrue(sources["s3"]["version_filled"])
        # 与 s2 同版本且取值一致：合并，不产生冲突
        self.assertEqual(row["v"], 2)
        self.assertEqual(result["conflicts"], [])

    def test_missing_version_before_first_explicit_loses(self):
        rows = [
            {"table": "t", "key": "a", "ts": 100, "v": 1, "source": "s1"},
            {"table": "t", "key": "a", "version": 2, "ts": 200, "v": 2, "source": "s2"},
        ]
        result = merge(rows)
        self.assertEqual(result["rows"][0]["v"], 2)
        self.assertEqual(result["rows"][0]["provenance"]["reason"], "higher_version")

    def test_stats_aggregated_per_table(self):
        merge([
            {"table": "t1", "key": "a", "version": 1, "v": 1, "source": "s1"},
            {"table": "t1", "key": "a", "version": 2, "v": 2, "source": "s2"},
            {"table": "t2", "key": "b", "version": 1, "v": 1, "source": "s1"},
        ])
        stats = report()["stats"]
        self.assertEqual(stats["t1"], {
            "input": 2, "kept": 1, "deduped": 1, "conflicts": 0, "dropped": 1,
        })
        self.assertEqual(stats["t2"]["deduped"], 0)
        self.assertEqual(report()["deduped"], 1)

    def test_replay_is_stable_and_does_not_mutate(self):
        rows = [
            {"table": "t", "key": "a", "version": 1, "amount": 1, "source": "s1"},
            {"table": "t", "key": "a", "version": 1, "amount": 2, "source": "s2"},
        ]
        first = merge(rows)
        before = report()
        self.assertEqual(replay(), first)
        self.assertEqual(replay(rows), first)
        self.assertEqual(report(), before)
        # 重复回放同一批数据不改变已有结果
        self.assertEqual(merge(rows), first)

    def test_export_keeps_sources_and_rows(self):
        rows = [
            {"table": "t", "key": "a", "version": 1, "v": 1, "source": "s1"},
            {"table": "t", "key": "a", "version": 2, "v": 2, "source": "s2"},
        ]
        merge(rows)
        exported = export()
        self.assertEqual(len(exported["records"]), 2)
        self.assertEqual(exported["rows"][0]["v"], 2)
        self.assertIn("t", exported["stats"])

    def test_report_dropped_counts(self):
        merge([
            {"table": "t", "key": "a", "version": 1, "v": 1, "source": "s1"},
            {"table": "t", "key": "a", "version": 2, "v": 2, "source": "s2"},
        ])
        summary = report()
        self.assertEqual(summary["dropped"], 1)
        self.assertEqual(summary["dropped_details"][0]["source"], "s1")


if __name__ == "__main__":
    unittest.main()
