import json
import os
import tempfile
import unittest

from crawlmerge2 import merge, report, replay, export, reset


class MergeTest(unittest.TestCase):
    def test_later_row_wins(self):
        first = {"rows": [{"key": "a", "value": 1}]}
        second = {"rows": [{"key": "a", "value": 2}]}
        report_data = merge([first, second])
        self.assertEqual(report_data["merged"], 1)
        self.assertEqual(report()["rows"], 1)

    def test_conflicts_not_reported(self):
        merge([{"rows": [{"key": "b", "v": 1}]}, {"rows": [{"key": "b", "v": 2}]}])
        self.assertEqual(report()["conflicts"], [])

    def test_empty_batches(self):
        self.assertEqual(merge([])["merged"], 0)


class VersionedMergeTest(unittest.TestCase):
    def setUp(self):
        reset()

    def tearDown(self):
        reset()

    def _row(self, key, **extra):
        row = {"key": key}
        row.update(extra)
        return row

    def test_higher_version_wins(self):
        old = {"batch": "b1", "rows": [self._row("a", version=1, value=1)]}
        new = {"batch": "b2", "rows": [self._row("a", version=3, value=9)]}
        result = merge([old, new])
        self.assertEqual(result["merged"], 1)
        winner = result["merged_rows"][0]
        self.assertEqual(winner["version"], 3)
        self.assertEqual(winner["decision"], "version_win")
        self.assertEqual(winner["row"]["raw"]["value"], 9)
        self.assertEqual(result["dropped"][0]["reason"], "lower_version")
        self.assertEqual(result["conflicts"], [])

    def test_lower_version_cannot_override_even_from_later_batch(self):
        newer = {"batch": "b1", "rows": [self._row("a", version=5, value=1)]}
        older = {"batch": "b2", "rows": [self._row("a", version=2, value=2)]}
        result = merge([newer, older])
        winner = result["merged_rows"][0]
        self.assertEqual(winner["version"], 5)
        self.assertEqual(winner["batch"], "b1")
        self.assertEqual(result["dropped"][0]["reason"], "lower_version")

    def test_source_priority_wins_on_same_version(self):
        weak = {"batch": "b1", "rows": [self._row("a", version=2, source="weak", v=1)]}
        strong = {"batch": "b2", "rows": [self._row("a", version=2, source="strong", v=2)]}
        result = merge([weak, strong], source_priority=["strong", "weak"])
        winner = result["merged_rows"][0]
        self.assertEqual(winner["source"], "strong")
        self.assertEqual(winner["decision"], "source_priority_win")
        self.assertEqual(result["dropped"][0]["reason"], "lower_source_priority")

    def test_source_priority_configurable_via_configure(self):
        configure_priority = ["strong", "weak"]
        weak = {"batch": "b1", "rows": [self._row("a", version=2, source="weak", v=1)]}
        strong = {"batch": "b2", "rows": [self._row("a", version=2, source="strong", v=2)]}
        from crawlmerge2 import configure
        configure(configure_priority)
        result = merge([weak, strong])
        self.assertEqual(result["merged_rows"][0]["source"], "strong")

    def test_same_source_different_batches_ordered_by_time(self):
        first = {"batch": "b1", "rows": [self._row(
            "a", version=1, source="s", time="2026-01-01T00:00:00", v=1)]}
        second = {"batch": "b2", "rows": [self._row(
            "a", version=1, source="s", time="2026-02-01T00:00:00", v=2)]}
        result = merge([first, second])
        winner = result["merged_rows"][0]
        self.assertEqual(winner["decision"], "time_win")
        self.assertEqual(winner["batch"], "b2")
        self.assertEqual(result["dropped"][0]["reason"], "older_time")

    def test_per_merge_priority_does_not_change_module_default(self):
        from crawlmerge2 import configure
        configure(["weak", "strong"])
        weak = {"batch": "b1", "rows": [self._row("a", version=1, source="weak", v=1)]}
        strong = {"batch": "b2", "rows": [self._row("a", version=1, source="strong", v=2)]}
        # 本次合并用临时配置，strong 胜出。
        result = merge([weak, strong], source_priority=["strong", "weak"])
        self.assertEqual(result["merged_rows"][0]["source"], "strong")
        # 模块默认配置不变，下一次合并仍按 weak 优先。
        result_default = merge([weak, strong])
        self.assertEqual(result_default["merged_rows"][0]["source"], "weak")

    def test_same_source_without_time_falls_back_to_batch_order(self):
        first = {"batch": "b1", "rows": [self._row("a", source="s", v=1)]}
        second = {"batch": "b2", "rows": [self._row("a", source="s", v=2)]}
        result = merge([first, second])
        winner = result["merged_rows"][0]
        self.assertEqual(winner["batch"], "b2")
        self.assertEqual(result["dropped"][0]["reason"], "older_batch")

    def test_conflict_keeps_both_original_rows(self):
        left = {"batch": "b1", "rows": [self._row(
            "a", version=2, source="left", v=1)]}
        right = {"batch": "b2", "rows": [self._row(
            "a", version=2, source="right", v=2)]}
        result = merge([left, right])
        self.assertEqual(len(result["conflicts"]), 1)
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["key"], "a")
        self.assertEqual(len(conflict["sides"]), 2)
        # 两侧原始行完整保留，且互不修改。
        side_sources = {side["source"] for side in conflict["sides"]}
        self.assertEqual(side_sources, {"left", "right"})
        side_values = {side["raw"]["v"] for side in conflict["sides"]}
        self.assertEqual(side_values, {1, 2})
        for side in conflict["sides"]:
            self.assertIn("raw", side)
            self.assertEqual(side["raw"]["key"], "a")
        # 合并结果仍有确定性赢家，标记为 conflict。
        winner = result["merged_rows"][0]
        self.assertEqual(winner["decision"], "conflict")
        self.assertEqual(winner["batch"], "b2")
        # 冲突行不算丢弃。
        self.assertEqual(result["dropped"], [])

    def test_configured_equal_priority_distinct_sources_conflict(self):
        left = {"batch": "b1", "rows": [self._row("a", version=1, source="x", v=1)]}
        right = {"batch": "b2", "rows": [self._row("a", version=1, source="y", v=2)]}
        result = merge([left, right], source_priority=["z"])
        self.assertEqual(len(result["conflicts"]), 1)

    def test_identical_payloads_are_duplicates_not_conflicts(self):
        left = {"batch": "b1", "rows": [self._row("a", version=1, source="x", v=1)]}
        right = {"batch": "b2", "rows": [self._row("a", version=1, source="y", v=1)]}
        result = merge([left, right])
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(result["dropped"][0]["reason"], "identical_duplicate")

    def test_missing_version_filled_and_flagged(self):
        row_batch = {"batch": "b1", "rows": [self._row("a", source="s", v=1)]}
        result = merge([row_batch])
        winner = result["merged_rows"][0]
        self.assertEqual(winner["version"], 0)
        self.assertTrue(winner["version_filled"])

    def test_missing_version_loses_to_any_real_version(self):
        missing = {"batch": "b1", "rows": [self._row("a", source="s", v=1)]}
        versioned = {"batch": "b2", "rows": [self._row("a", version=1, source="s", v=2)]}
        result = merge([missing, versioned])
        winner = result["merged_rows"][0]
        self.assertEqual(winner["version"], 1)
        self.assertEqual(result["dropped"][0]["reason"], "lower_version")

    def test_missing_version_rows_ordered_by_source_priority(self):
        weak = {"batch": "b1", "rows": [self._row("a", source="weak", v=1)]}
        strong = {"batch": "b2", "rows": [self._row("a", source="strong", v=2)]}
        result = merge([weak, strong], source_priority=["strong", "weak"])
        winner = result["merged_rows"][0]
        self.assertTrue(winner["version_filled"])
        self.assertEqual(winner["source"], "strong")
        self.assertEqual(winner["decision"], "source_priority_win")

    def test_report_lists_source_and_decision_per_row(self):
        b1 = {"batch": "b1", "rows": [
            self._row("a", version=2, source="s1", v=1),
            self._row("b", version=1, source="s2", v=3),
        ]}
        b2 = {"batch": "b2", "rows": [self._row("a", version=1, source="s2", v=9)]}
        merge([b1, b2])
        rep = report()
        self.assertEqual(rep["rows"], 2)
        entries = {row["key"]: row for row in rep["merged"]}
        self.assertEqual(entries["a"]["source"], "s1")
        self.assertEqual(entries["a"]["decision"], "version_win")
        self.assertEqual(entries["b"]["source"], "s2")
        self.assertEqual(entries["b"]["decision"], "sole")
        reasons = {(d["key"], d["reason"]) for d in rep["dropped"]}
        self.assertIn(("a", "lower_version"), reasons)

    def test_replay_recomputes_same_result(self):
        b1 = {"batch": "b1", "rows": [
            self._row("a", version=1, source="left", v=1),
            self._row("b", version=1, source="s", time=100, v=2),
        ]}
        b2 = {"batch": "b2", "rows": [
            self._row("a", version=1, source="right", v=9),
            self._row("b", version=1, source="s", time=200, v=3),
            self._row("c", version=2, v=7),
        ]}
        result = merge([b1, b2], source_priority=["s"])
        replayed = replay()
        self.assertEqual(replayed, result)

    def test_replay_from_exported_file(self):
        b1 = {"batch": "b1", "rows": [self._row("a", version=1, source="left", v=1)]}
        b2 = {"batch": "b2", "rows": [self._row("a", version=1, source="right", v=2)]}
        result = merge([b1, b2])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "export.json")
            returned = export(path)
            self.assertEqual(returned, path)
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(payload["result"], result)
            reset()
            replayed = replay(path)
        self.assertEqual(replayed, result)
        self.assertEqual(len(replayed["conflicts"]), 1)

    def test_replay_uses_same_config_snapshot(self):
        # 回放后再改配置，不影响快照里的判定。
        b1 = {"batch": "b1", "rows": [self._row("a", version=1, source="weak", v=1)]}
        b2 = {"batch": "b2", "rows": [self._row("a", version=1, source="strong", v=2)]}
        result = merge([b1, b2], source_priority=["strong", "weak"])
        from crawlmerge2 import configure
        configure(["weak", "strong"])
        replayed = replay()
        self.assertEqual(replayed, result)
        self.assertEqual(replayed["merged_rows"][0]["source"], "strong")

    def test_export_returns_json_string_without_path(self):
        merge([{"batch": "b1", "rows": [self._row("a", version=1, v=1)]}])
        text = export()
        payload = json.loads(text)
        self.assertIn("snapshot", payload)
        self.assertIn("result", payload)
        self.assertEqual(payload["result"]["merged"], 1)

    def test_each_row_carries_batch_version_source(self):
        batch = {"batch": "b9", "rows": [self._row("a", version=4, source="src", v=1)]}
        result = merge([batch])
        row = result["merged_rows"][0]["row"]
        self.assertEqual(row["batch"], "b9")
        self.assertEqual(row["version"], 4)
        self.assertEqual(row["source"], "src")
        self.assertEqual(row["raw"], {"key": "a", "version": 4, "source": "src", "v": 1})


if __name__ == "__main__":
    unittest.main()
