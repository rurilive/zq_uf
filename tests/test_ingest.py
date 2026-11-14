import importlib
import unittest

from quarantine import (
    export_samples,
    ingest,
    quarantine_list,
    replay,
    reset,
)

module = importlib.import_module("quarantine.ingest")


class IngestTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_good_batch(self):
        self.assertEqual(ingest([{"id": 1}])["ingested"], 1)

    def test_one_bad_row_aborts_all(self):
        with self.assertRaises(ValueError):
            ingest([{"id": 1}, {"id": 2, "bad": True}], legacy=True)
        self.assertEqual(len(module._store), 0)

    def test_replay_not_supported(self):
        with self.assertRaises(NotImplementedError):
            replay("b1")

    def test_bad_rows_are_quarantined_and_good_rows_ingested(self):
        result = ingest(
            [{"id": 1}, {"id": 2, "bad": True}, {"id": 3}],
            batch_id="b1",
        )
        self.assertEqual(result["ingested"], 2)
        self.assertEqual(result["quarantined"], 1)
        self.assertEqual(result["reason_dist"], {"flagged_bad": 1})
        self.assertEqual(len(module._store), 2)

        records = quarantine_list(batch_id="b1")
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["batch_id"], "b1")
        self.assertEqual(record["reason"], "flagged_bad")
        # 原始内容完整保留
        self.assertEqual(record["raw"], {"id": 2, "bad": True})
        self.assertEqual(record["position"], 1)
        self.assertEqual(record["status"], "quarantined")

    def test_reason_distribution_and_indexes(self):
        ingest(
            [
                {"id": 1, "bad": True},
                {"bad": True},
                {"id": 3},
            ],
            batch_id="b2",
        )
        self.assertEqual(len(quarantine_list(reason="flagged_bad")), 2)
        self.assertEqual(len(quarantine_list(batch_id="b2")), 2)
        self.assertEqual(len(quarantine_list(batch_id="b2", reason="flagged_bad")), 2)

    def test_replay_fixed_rows_by_batch(self):
        ingest([{"id": 1}, {"id": 2, "bad": True}], batch_id="b3")
        result = replay("b3", fixed_rows=[{"id": 2}])
        self.assertEqual(result["replayed"], 1)
        self.assertEqual(result["duplicates"], 0)
        self.assertEqual(result["still_bad"], 0)
        self.assertIn("id:2", module._store)
        # 隔离记录标记为已重放
        self.assertEqual(quarantine_list(batch_id="b3")[0]["status"], "replayed")

    def test_replay_is_idempotent(self):
        ingest([{"id": 1}, {"id": 2, "bad": True}], batch_id="b4")
        first = replay("b4", fixed_rows=[{"id": 2}])
        stored_after_first = len(module._store)
        second = replay("b4", fixed_rows=[{"id": 2}])
        self.assertEqual(first["replayed"], 1)
        self.assertEqual(second["replayed"], 0)
        self.assertEqual(second["duplicates"], 1)
        # 已入库的行不重复
        self.assertEqual(len(module._store), stored_after_first)

        # 直接重入相同好行也不重复入库
        again = ingest([{"id": 1}, {"id": 2}], batch_id="b4-repeat")
        self.assertEqual(again["ingested"], 0)

    def test_replay_reconciles_with_original_batch(self):
        ingest(
            [
                {"id": 1},
                {"id": 2, "bad": True},
                {"id": 3, "bad": True},
            ],
            batch_id="b5",
        )
        result = replay("b5", fixed_rows=[(1, {"id": 2}), (2, {"id": 3})])
        recon = result["reconciliation"]
        self.assertEqual(recon["original_total"], 3)
        self.assertEqual(recon["original_ingested"], 1)
        self.assertEqual(recon["original_quarantined"], 2)
        self.assertEqual(recon["replayed_ok"], 2)
        self.assertTrue(recon["balanced"])

    def test_replay_keeps_still_bad_rows_quarantined(self):
        ingest([{"id": 1, "bad": True}], batch_id="b6")
        # 不传修复行，原始坏行重放后仍不合格
        result = replay("b6")
        self.assertEqual(result["replayed"], 0)
        self.assertEqual(result["still_bad"], 1)
        self.assertEqual(result["reason_dist"], {"flagged_bad": 1})
        self.assertEqual(len(quarantine_list(batch_id="b6")), 1)
        recon = result["reconciliation"]
        self.assertEqual(recon["still_quarantined"], 1)
        # 数据未丢失：好行 + 新入库 + 重复 + 仍隔离 = 原批总数
        self.assertTrue(recon["balanced"])
        # 修复后再次重放即可对账通过
        fixed = replay("b6", fixed_rows=[{"id": 1}])
        self.assertTrue(fixed["reconciliation"]["balanced"])

    def test_export_samples_by_reason(self):
        ingest(
            [
                {"id": 1, "bad": True},
                {"id": 2, "bad": True},
                {"id": 3},
            ],
            batch_id="b7",
        )
        samples = export_samples("flagged_bad")
        self.assertEqual(
            samples,
            [{"id": 1, "bad": True}, {"id": 2, "bad": True}],
        )
        self.assertEqual(len(export_samples("flagged_bad", limit=1)), 1)
        self.assertEqual(export_samples("missing_id"), [])

    def test_auto_batch_id_is_returned(self):
        result = ingest([{"id": 1}])
        self.assertTrue(result["batch_id"].startswith("batch-"))

    def test_non_dict_row_quarantined_and_replay_by_record_id(self):
        ingest([{"id": 1}, "junk-row"], batch_id="b8")
        record = quarantine_list(batch_id="b8")[0]
        self.assertEqual(record["reason"], "not_a_dict")
        self.assertEqual(record["raw"], "junk-row")
        result = replay("b8", fixed_rows=[(record["record_id"], {"id": 8})])
        self.assertEqual(result["replayed"], 1)
        self.assertTrue(result["reconciliation"]["balanced"])

    def test_batches_are_indexed_independently(self):
        ingest([{"id": 1, "bad": True}], batch_id="b9a")
        ingest([{"id": 2, "bad": True}], batch_id="b9b")
        self.assertEqual(len(quarantine_list(batch_id="b9a")), 1)
        self.assertEqual(len(quarantine_list(batch_id="b9b")), 1)
        result = replay("b9a", fixed_rows=[{"id": 1}])
        self.assertEqual(result["replayed"], 1)
        self.assertEqual(len(quarantine_list(batch_id="b9b")), 1)


if __name__ == "__main__":
    unittest.main()
