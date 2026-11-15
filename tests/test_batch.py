import json
import os
import tempfile
import unittest

from changebatch import (
    apply,
    export_rollbacks,
    journal,
    pending,
    reset,
    resume,
    snapshot,
    watermark,
)


def reconcile():
    """对账口径：行数 + 数值合计。"""
    table = snapshot()
    return {"rows": len(table), "sum": sum(table.values())}


class BatchTest(unittest.TestCase):
    def setUp(self):
        reset()

    # --- 整批成功 ---

    def test_apply_all_rows(self):
        result = apply({"id": "b1", "seq": 1,
                        "rows": [{"key": "a", "value": 1},
                                 {"key": "b", "value": 2}]})
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["applied"], 2)
        self.assertEqual(result["rolled_back"], 0)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(snapshot(), {"a": 1, "b": 2})
        self.assertEqual(watermark(), 1)

    # --- 失败整批回滚 ---

    def test_failure_rolls_back_whole_batch(self):
        apply({"id": "seed", "seq": 1, "rows": [{"key": "a", "value": 1}]})
        before = snapshot()

        result = apply({"id": "b2", "seq": 2,
                        "rows": [{"key": "b", "value": 2},
                                 {"key": "c", "value": 3},
                                 {"bad": True, "key": "d"}]})

        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(result["applied"], 0)
        self.assertEqual(result["rolled_back"], 2)
        self.assertEqual(snapshot(), before)   # 表回到批次开始前
        self.assertEqual(watermark(), 1)       # 失败不推进水线
        self.assertEqual(result["watermark"], 1)

    def test_rollback_restores_overwritten_values_in_reverse(self):
        apply({"id": "seed", "seq": 1,
               "rows": [{"key": "a", "value": 1}, {"key": "b", "value": 2}]})
        result = apply({"id": "b2", "seq": 2,
                        "rows": [{"key": "a", "value": 10},
                                 {"key": "b", "value": 20},
                                 {"bad": True, "key": "c"}]})
        self.assertEqual(result["rolled_back"], 2)
        self.assertEqual(snapshot(), {"a": 1, "b": 2})
        # 明细按写入的逆序回放
        keys = [row["key"] for row in result["rollback"]["rows"]]
        self.assertEqual(keys, ["b", "a"])
        self.assertEqual(result["rollback"]["rows"][0]["restored"], 2)
        self.assertEqual(result["rollback"]["rows"][1]["restored"], 1)

    def test_reconciliation_after_rollback(self):
        apply({"id": "seed", "seq": 1,
               "rows": [{"key": "a", "value": 100},
                        {"key": "b", "value": 200}]})
        before = reconcile()
        result = apply({"id": "b2", "seq": 2,
                        "rows": [{"key": "a", "value": 1},
                                 {"key": "c", "value": 3},
                                 {"bad": True, "key": "d"}]})
        self.assertEqual(result["status"], "rolled_back")
        self.assertEqual(reconcile(), before)

    # --- 重复提交只应用一次 ---

    def test_duplicate_batch_applied_once(self):
        batch = {"id": "b1", "seq": 1, "rows": [{"key": "a", "value": 1}]}
        first = apply(batch)
        second = apply(batch)

        self.assertEqual(first["applied"], 1)
        self.assertEqual(second["status"], "skipped")
        self.assertEqual(second["applied"], 0)
        self.assertEqual(second["skipped"], 1)
        self.assertEqual(snapshot(), {"a": 1})

    def test_duplicate_position_skipped(self):
        apply({"id": "b1", "seq": 1, "rows": [{"key": "a", "value": 1}]})
        other = apply({"id": "b1-retry", "seq": 1,
                       "rows": [{"key": "a", "value": 99}]})
        self.assertEqual(other["status"], "skipped")
        self.assertEqual(other["skipped"], 1)
        self.assertEqual(snapshot(), {"a": 1})

    # --- 冲突保留两侧 ---

    def test_conflict_keeps_both_sides(self):
        apply({"id": "seed", "seq": 1, "rows": [{"key": "a", "value": 1}]})
        result = apply({"id": "b2", "seq": 2,
                        "rows": [{"key": "a", "value": 2},
                                 {"key": "b", "value": 3}]})

        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["applied"], 2)
        self.assertEqual(len(result["conflicts"]), 1)
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["key"], "a")
        self.assertEqual(conflict["existing"], 1)
        self.assertEqual(conflict["incoming"], 2)
        # 两侧值都可回看；表内落新值
        self.assertEqual(snapshot(), {"a": 2, "b": 3})
        self.assertEqual(export_rollbacks(), [])

    def test_conflict_reports_both_sides_when_batch_rolls_back(self):
        apply({"id": "seed", "seq": 1, "rows": [{"key": "a", "value": 1}]})
        result = apply({"id": "b2", "seq": 2,
                        "rows": [{"key": "a", "value": 2},
                                 {"key": "b", "value": 3},
                                 {"bad": True, "key": "c"}]})

        self.assertEqual(result["status"], "rolled_back")
        conflict = result["conflicts"][0]
        self.assertEqual((conflict["existing"], conflict["incoming"]), (1, 2))
        self.assertEqual(conflict["resolution"],
                         "reverted with batch rollback")
        self.assertEqual(snapshot(), {"a": 1})   # 旧值还回去了

    # --- 顺序与缺口 ---

    def test_out_of_order_batch_blocked_not_gap(self):
        apply({"id": "b1", "seq": 1, "rows": [{"key": "a", "value": 1}]})
        blocked = apply({"id": "b3", "seq": 3,
                         "rows": [{"key": "c", "value": 3}]})
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["applied"], 0)
        self.assertEqual(snapshot(), {"a": 1})
        self.assertEqual(list(pending()), ["b3"])

        apply({"id": "b2", "seq": 2, "rows": [{"key": "b", "value": 2}]})
        self.assertEqual(watermark(), 2)
        self.assertEqual(list(pending()), ["b3"])   # 越位批仍在等待

        drained = resume("b3")
        self.assertTrue(drained["resumed"])
        self.assertEqual(drained["applied"], 1)
        self.assertEqual(snapshot(), {"a": 1, "b": 2, "c": 3})
        self.assertEqual(watermark(), 3)
        self.assertEqual(pending(), {})

    # --- 恢复 ---

    def test_resume_continues_after_last_success(self):
        apply({"id": "b1", "seq": 1, "rows": [{"key": "a", "value": 1}]})
        fail = apply({"id": "b2", "seq": 2,
                      "rows": [{"key": "b", "value": 2},
                               {"key": "c", "value": 3}],
                      "fail_times": 1})
        self.assertEqual(fail["status"], "rolled_back")
        self.assertEqual(fail["rolled_back"], 1)
        blocked = apply({"id": "b3", "seq": 3,
                         "rows": [{"key": "d", "value": 4}]})
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(snapshot(), {"a": 1})
        self.assertEqual(watermark(), 1)

        resumed = resume("b2")
        self.assertTrue(resumed["resumed"])
        self.assertEqual(resumed["applied"], 3)   # b2 的 b、c + b3 的 d
        self.assertEqual(snapshot(), {"a": 1, "b": 2, "c": 3, "d": 4})
        self.assertEqual(watermark(), 3)
        self.assertEqual(pending(), {})

    def test_resume_retries_from_last_successful_batch(self):
        apply({"id": "b1", "seq": 1, "rows": [{"key": "a", "value": 1}]})
        apply({"id": "b2", "seq": 2,
               "rows": [{"key": "b", "value": 2},
                        {"key": "c", "value": 3}],
               "fail_times": 2})
        self.assertEqual(watermark(), 1)

        # 故障未排除时恢复：再次回滚，水线仍停在 1
        blocked = resume("b2")
        self.assertFalse(blocked["resumed"])
        self.assertEqual(blocked["rolled_back"], 1)
        self.assertEqual(watermark(), 1)
        self.assertEqual(snapshot(), {"a": 1})
        self.assertIn("b2", pending())

        # 第三次提交不再注入故障，恢复成功
        ok = resume("b2")
        self.assertTrue(ok["resumed"])
        self.assertEqual(snapshot(), {"a": 1, "b": 2, "c": 3})
        self.assertEqual(watermark(), 2)

    def test_resume_is_idempotent(self):
        apply({"id": "b1", "seq": 1, "rows": [{"key": "a", "value": 1}]})
        apply({"id": "b2", "seq": 2,
               "rows": [{"key": "b", "value": 2},
                        {"key": "c", "value": 3}],
               "fail_times": 1})
        first = resume("b2")
        again = resume("b2")
        self.assertTrue(first["resumed"])
        self.assertTrue(again["resumed"])
        self.assertEqual(again["applied"], 0)
        self.assertEqual(again["skipped"], 0)
        self.assertEqual(snapshot(), {"a": 1, "b": 2, "c": 3})

    # --- 审计 ---

    def test_audit_journal_and_export(self):
        apply({"id": "b1", "seq": 1, "rows": [{"key": "a", "value": 1}]})
        apply({"id": "b1", "seq": 1, "rows": [{"key": "a", "value": 1}]})
        apply({"id": "b2", "seq": 2,
               "rows": [{"key": "b", "value": 2},
                        {"key": "c", "value": 3}],
               "fail_times": 1})
        resume("b2")

        events = [entry["event"] for entry in journal()]
        self.assertEqual(events, ["applied", "skipped_duplicate",
                                  "rolled_back", "applied", "resumed"])

        entries = export_rollbacks()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["batch"], "b2")
        self.assertEqual(entries[0]["attempt"], 1)
        self.assertEqual(entries[0]["error"], "第 1 行（key=c）写入失败：注入故障")
        self.assertEqual([row["key"] for row in entries[0]["rows"]], ["b"])

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rollbacks.json")
            export_rollbacks(path)
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle), entries)

    # --- 结构校验 ---

    def test_missing_key_rejects_batch(self):
        with self.assertRaises(ValueError):
            apply({"id": "b1", "seq": 1, "rows": [{"value": 1}]})
        self.assertEqual(snapshot(), {})
        self.assertEqual(watermark(), 0)

    def test_missing_id_rejected(self):
        with self.assertRaises(ValueError):
            apply({"seq": 1, "rows": []})


if __name__ == "__main__":
    unittest.main()
