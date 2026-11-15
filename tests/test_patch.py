import unittest

from fieldpatch import (
    patch, run_batch, report, rollback, replay,
    export_patches, reset, Store, _default_store,
)


SCHEMA = {
    "fields": {
        "title": {"type": "string", "required": True},
        "price": {"type": "number", "required": True},
        "tag": {"type": "string"},                      # 可空，默认 ""
        "count": {"type": "number"},                    # 可空数字，默认 0
        "status": {"type": "enum", "values": ["new", "used"],
                   "degrade": "unknown"},
    }
}


class RowPatchTest(unittest.TestCase):
    def test_complete_row_untouched(self):
        row = {"id": 1, "title": "x", "price": 10, "status": "new",
               "tag": "t", "count": 3}
        res = patch(row, SCHEMA)
        self.assertFalse(res["is_isolated"])
        self.assertEqual(res["patched"], [])
        self.assertEqual(res["sources"]["title"], "original")
        # 未声明字段原样透传
        self.assertEqual(res["row"]["id"], 1)

    def test_required_missing_isolates_field_not_whole_batch(self):
        res = patch({"title": "x"}, SCHEMA)  # 缺必填 price
        self.assertTrue(res["is_isolated"])
        self.assertEqual(res["isolated"], ["price"])
        self.assertNotIn("price", res["row"])
        # 同一条里其余字段仍然补齐，没有整行丢弃
        self.assertIn("title", res["row"])

    def test_nullable_missing_gets_typed_default_not_empty_string(self):
        res = patch({"title": "x", "price": 1}, SCHEMA)
        self.assertEqual(res["row"]["tag"], "")
        self.assertEqual(res["row"]["count"], 0)  # 数字补 0，不是空串
        self.assertEqual(res["sources"]["count"], "default")
        self.assertIn("count", res["patched"])

    def test_explicit_none_treated_as_missing(self):
        res = patch({"title": "x", "price": 1, "count": None}, SCHEMA)
        self.assertEqual(res["row"]["count"], 0)
        self.assertEqual(res["fields"]["count"]["reason"], "missing")

    def test_numeric_empty_string_never_becomes_empty_in_row(self):
        # 空串对数字字段是非法类型：必填隔离，可空补类型安全默认值
        res = patch({"title": "x", "price": ""}, SCHEMA)
        self.assertIn("price", res["isolated"])
        res2 = patch({"title": "x", "price": 1, "count": ""}, SCHEMA)
        self.assertEqual(res2["row"]["count"], 0)
        self.assertEqual(res2["fields"]["count"]["reason"], "invalid_type")

    def test_numeric_string_coerced_with_original_kept(self):
        res = patch({"title": "x", "price": "12.5"}, SCHEMA)
        self.assertEqual(res["row"]["price"], 12.5)
        self.assertEqual(res["fields"]["price"]["original"], "12.5")
        self.assertEqual(res["fields"]["price"]["action"], "coerce")

    def test_enum_out_of_range_degrades_and_records(self):
        res = patch({"title": "x", "price": 1, "status": "bogus"}, SCHEMA)
        self.assertEqual(res["row"]["status"], "unknown")
        self.assertEqual(res["sources"]["status"], "degrade")
        self.assertEqual(res["fields"]["status"]["original"], "bogus")
        self.assertEqual(res["fields"]["status"]["reason"],
                         "enum_out_of_range")

    def test_legacy_top_level_required_list(self):
        res = patch({"a": 1}, {"required": ["a", "b"]})
        self.assertEqual(res["isolated"], ["b"])
        self.assertEqual(res["row"]["a"], 1)


class BatchTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.rows = [
            {"id": 1, "title": "ok", "price": 10, "status": "new"},
            {"id": 2, "title": "no-price"},                       # 隔离
            {"id": 3, "title": "fill", "price": 5, "count": "7",
             "status": "weird"},                                  # 修补+降级
        ]

    def test_one_bad_record_does_not_drop_good_records(self):
        out = run_batch(self.rows, SCHEMA, site="s1", day="2026-11-16")
        self.assertEqual(out["summary"]["rows"], 3)
        # 字段齐全的记录正常入库
        self.assertIn(1, _default_store.records)
        self.assertIn(3, _default_store.records)
        # 仅缺必填的记录被隔离
        quarantined = [q["record_id"]
                       for q in _default_store.quarantine]
        self.assertEqual(quarantined, [2])

    def test_report_aggregates_by_day_and_site_with_missing_ratio(self):
        run_batch(self.rows, SCHEMA, site="s1", day="2026-11-16")
        rep = report()
        self.assertIn("2026-11-16|s1", rep["by_day_site"])
        agg = rep["by_day_site"]["2026-11-16|s1"]
        # 缺失按字段统计（含可空与必填）：声明字段 3×5=15，另含 3 个透传 id
        # 透传字段不可能缺失，故 missing 不变、分母变大
        self.assertEqual(agg["total_fields"], 18)
        self.assertEqual(agg["missing"], 7)
        self.assertAlmostEqual(agg["missing_ratio"], 7 / 18, places=4)
        # 其中仅 1 个是必填缺失（record 2 的 price），对应 1 条隔离
        self.assertEqual(agg["missing_required"], 1)
        self.assertEqual(agg["isolated"], 1)
        # 来源统计包含 default/degrade/original
        self.assertIn("default", rep["sources"])
        self.assertIn("degrade", rep["sources"])

    def test_report_filter_by_site(self):
        run_batch(self.rows, SCHEMA, site="s1", day="2026-11-16")
        run_batch(self.rows, SCHEMA, site="s2", day="2026-11-16")
        self.assertEqual(report(site="s1")["batches"], 1)
        self.assertEqual(report(site="none")["batches"], 0)

    def test_alert_when_missing_ratio_exceeds_threshold(self):
        store = Store(missing_threshold=0.0)
        out = run_batch(self.rows, SCHEMA, site="s1",
                        day="2026-11-16", store=store)
        self.assertTrue(out["alerts"])
        # 阈值很高时不应告警
        quiet = Store(missing_threshold=0.99)
        out2 = run_batch(self.rows, SCHEMA, site="s1",
                         day="2026-11-16", store=quiet)
        self.assertFalse(out2["alerts"])

    def test_dry_run_does_not_persist(self):
        store = Store()
        out = run_batch(self.rows, SCHEMA, site="s1",
                        day="2026-11-16", dry_run=True, store=store)
        self.assertIsNone(out["batch_id"])
        self.assertTrue(out["dry_run"])
        self.assertEqual(store.report()["batches"], 0)
        self.assertEqual(store.records, {})
        self.assertEqual(store.quarantine, [])

    def test_rollback_restores_originals_and_clears_quarantine(self):
        from fieldpatch import _default_store
        out = run_batch(self.rows, SCHEMA, site="s1", day="2026-11-16")
        bid = out["batch_id"]
        # 修补后 record 3 的 count 被收敛、status 被降级
        self.assertEqual(_default_store.records[3]["count"], 7)
        self.assertEqual(_default_store.records[3]["status"], "unknown")

        rb = rollback(bid)
        self.assertIn(3, rb["restored"])
        self.assertEqual(rb["dequarantined"], 1)
        # 原值恢复：收敛字段还原为原字符串、降级字段还原、原本缺失的字段移除
        self.assertEqual(_default_store.records[3]["status"], "weird")
        self.assertEqual(_default_store.records[3]["count"], "7")
        self.assertNotIn("tag", _default_store.records[3])
        self.assertEqual(_default_store.quarantine, [])
        # 回滚后统计不再计入该批
        self.assertEqual(report()["patched"], 0)

    def test_rollback_then_replay_is_symmetric(self):
        from fieldpatch import _default_store
        out = run_batch(self.rows, SCHEMA, site="s1", day="2026-11-16")
        bid = out["batch_id"]
        rollback(bid)
        with self.assertRaises(ValueError):
            rollback(bid)  # 已回滚不能重复回滚

        rp = replay(bid)
        self.assertIn(3, rp["replayed"])
        self.assertIn(2, rp["requarantined"])
        self.assertEqual(_default_store.records[3]["status"], "unknown")
        self.assertEqual(len(_default_store.quarantine), 1)
        self.assertTrue(report()["patched"] > 0)
        with self.assertRaises(ValueError):
            replay(bid)  # 活跃批次不能重复回放

    def test_export_dict_and_csv_filterable(self):
        out = run_batch(self.rows, SCHEMA, site="s1", day="2026-11-16")
        bid = out["batch_id"]
        all_rows = export_patches()
        self.assertTrue(all_rows)
        # 每条留痕带字段来源与原值
        sample = next(r for r in all_rows if r["field"] == "status"
                      and r["record_id"] == 3)
        self.assertEqual(sample["source"], "degrade")
        self.assertEqual(sample["original"], "weird")
        # 按批次/站点过滤
        self.assertEqual(
            {r["batch_id"] for r in export_patches(batch_id=bid)}, {bid}
        )
        csv_text = export_patches(site="s1", fmt="csv")
        self.assertIn("batch_id,day,site", csv_text)
        self.assertIn("unknown", csv_text)


if __name__ == "__main__":
    unittest.main()
