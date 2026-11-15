import unittest

from partlife import (
    Store,
    apply,
    plan,
    query_archive,
    query_cold,
    recover,
    retry_batch,
)

PARTS = [
    {"id": "p-hot", "rows": 10, "last_access_days": 1},
    {"id": "p-cold", "rows": 20, "last_access_days": 20},
    {"id": "p-a1", "rows": 30, "last_access_days": 90},
    {"id": "p-a2", "rows": 40, "last_access_days": 90},
    {"id": "p-a3", "rows": 50, "last_access_days": 90},
    {"id": "p-leased", "rows": 60, "last_access_days": 90, "active_reads": 1},
]


def seeded():
    store = Store()
    store.seed("d", PARTS)
    return store


class LifeTest(unittest.TestCase):
    # 旧的按天行为保持兼容
    def test_plan_lists_all(self):
        self.assertEqual(plan({"name": "d", "days": ["p1", "p2"]})["expired"], ["p1", "p2"])

    def test_no_tiers(self):
        self.assertEqual(plan({"name": "d", "days": []})["tiers"], {})

    def test_apply_single_batch(self):
        self.assertEqual(apply({"expired": ["p1"]})["batches"], 1)

    # 分层与跳过
    def test_tiers_by_access(self):
        p = plan({"name": "d", "partitions": PARTS})
        self.assertEqual(p["tiers"]["hot"], ["p-hot"])
        self.assertEqual(p["tiers"]["cold"], ["p-cold"])
        self.assertEqual(p["tiers"]["archive"], ["p-a1", "p-a2", "p-a3"])
        self.assertEqual(p["skipped"], ["p-leased"])

    def test_apply_skips_leased_at_execution(self):
        store = seeded()
        r = apply(plan({"name": "d", "partitions": PARTS}), store)
        self.assertIn("p-leased", r["skipped"])
        self.assertEqual(store.hot[("d", "p-leased")], 60)  # 在读的分区没动

    # 冷层迁移与范围查询
    def test_cold_tier_range_query(self):
        store = seeded()
        r = apply(plan({"name": "d", "partitions": PARTS}), store)
        self.assertEqual(r["cold_locations"], {"p-cold": "cold://d/p-cold"})
        self.assertEqual(query_cold(store, "d", "p-a", "p-z"), ["p-cold"])
        self.assertEqual(query_cold(store, "d", "p-d", "p-z"), [])

    # 归档与删除分开记录，删除分批
    def test_archive_then_delete_in_batches(self):
        store = seeded()
        r = apply(plan({"name": "d", "partitions": PARTS}), store)
        self.assertEqual(r["batches"], 2)  # 3 个归档分区，batch_size=2
        self.assertEqual(sorted(r["deleted"]), ["p-a1", "p-a2", "p-a3"])
        self.assertEqual([a["location"] for a in r["archived"]],
                         ["archive://d/" + p for p in ("p-a1", "p-a2", "p-a3")])
        self.assertEqual(len(store.archive_log), 3)
        self.assertEqual(len(store.delete_log), 2)  # 归档、删除分别记录
        self.assertNotIn(("d", "p-a1"), store.hot)

    # 批次失败回滚，可单独重试
    def test_failed_batch_rolls_back_and_retries(self):
        store = seeded()
        store.fail_deletes.add("p-a3")  # 第二批失败
        r = apply(plan({"name": "d", "partitions": PARTS}), store)
        self.assertEqual(r["batch_records"][1]["status"], "rolled_back")
        self.assertEqual(sorted(r["deleted"]), ["p-a1", "p-a2"])
        store.fail_deletes.clear()
        rec = retry_batch(store, "d", 2)
        self.assertEqual(rec["status"], "done")
        self.assertNotIn(("d", "p-a3"), store.hot)

    # 归档范围查询与恢复后行数校验
    def test_archive_query_and_recover_verifies_rows(self):
        store = seeded()
        apply(plan({"name": "d", "partitions": PARTS}), store)
        self.assertEqual(query_archive(store, "d", "p-a1", "p-a2"),
                         ["archive://d/p-a1", "archive://d/p-a2"])
        back = recover(store, "d", "p-a1", expected_rows=30)
        self.assertTrue(back["restored"])
        self.assertTrue(back["rows_verified"])
        self.assertEqual(store.hot[("d", "p-a1")], 30)
        bad = recover(store, "d", "p-a2", expected_rows=999)
        self.assertFalse(bad["restored"])  # 行数不符，判为未恢复成功

    def test_recover_unarchived(self):
        self.assertEqual(recover(seeded(), "d", "nope")["reason"], "not_archived")


if __name__ == "__main__":
    unittest.main()
