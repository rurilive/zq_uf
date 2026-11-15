import glob
import importlib
import os
import tempfile
import unittest

from crawlmerge import merge, resume

module = importlib.import_module("crawlmerge.merge")


class MergeTest(unittest.TestCase):
    def setUp(self):
        module._rows.clear()

    def test_merge_adds(self):
        self.assertEqual(merge([[{"id": 1, "v": 1}]])["rows"], 1)

    def test_later_shard_wins(self):
        merge([[{"id": 1, "v": 2}]])
        merge([[{"id": 1, "v": 1}]])
        self.assertEqual(module._rows[1]["v"], 1)

    def test_resume_zero(self):
        self.assertEqual(resume()["done"], 0)


class VersionMergeTest(unittest.TestCase):
    def setUp(self):
        module._reset()
        self.tmp = tempfile.mkdtemp()

    def _merge(self, shards, batch="t"):
        return merge(shards, batch=batch, state_dir=self.tmp)

    def test_higher_version_wins_even_when_late(self):
        # 乱序：旧版本先到、新版本后到，再让旧版本回放也不能盖回去
        self._merge([[{"id": "a", "version": 1, "v": "old"}]])
        self._merge([[{"id": "a", "version": 3, "v": "new"}]])
        self._merge([[{"id": "a", "version": 2, "v": "mid"}]])
        row = module._rows["a"]
        self.assertEqual(row["version"], 3)
        self.assertEqual(row["v"], "new")

    def test_lower_version_fills_empty_only(self):
        self._merge([[{"id": "a", "version": 2, "v": "x", "extra": None}]])
        self._merge([[{"id": "a", "version": 1, "v": "stale", "extra": "fill"}]])
        row = module._rows["a"]
        self.assertEqual(row["v"], "x")        # 低版本有效值不覆盖高版本
        self.assertEqual(row["extra"], "fill")  # 高版本空字段由低版本补齐

    def test_null_never_overwrites_value(self):
        self._merge([[{"id": "a", "version": 1, "v": "keep", "w": 1}]])
        self._merge([[{"id": "a", "version": 2, "v": None, "w": None}]])
        row = module._rows["a"]
        self.assertEqual(row["v"], "keep")
        self.assertEqual(row["w"], 1)
        self.assertEqual(row["version"], 2)

    def test_missing_version_treated_as_zero(self):
        self._merge([[{"id": "a", "v": "old"}]])
        self._merge([[{"id": "a", "version": 1, "v": "new"}]])
        self.assertEqual(module._rows["a"]["v"], "new")

    def test_same_version_conflict_keeps_both_sides(self):
        result = self._merge([[
            {"id": "a", "version": 1, "v": "left"},
            {"id": "a", "version": 1, "v": "right"},
        ]])
        # 主表后到先写，空值保护仍然生效
        self.assertEqual(module._rows["a"]["v"], "right")
        self.assertEqual(len(result["conflicts"]), 1)
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["id"], "a")
        values = [side["row"]["v"] for side in conflict["sides"]]
        self.assertEqual(sorted(values), ["left", "right"])

    def test_same_version_null_difference_is_not_conflict(self):
        result = self._merge([[
            {"id": "a", "version": 1, "v": "x"},
            {"id": "a", "version": 1, "v": None},
        ]])
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(module._rows["a"]["v"], "x")

    def test_conflicts_aggregated_by_primary_key(self):
        result = self._merge([[
            {"id": "b", "version": 1, "v": 1},
            {"id": "b", "version": 1, "v": 2},
            {"id": "b", "version": 1, "v": 3},
            {"id": "a", "version": 1, "v": 1},
            {"id": "a", "version": 1, "v": 2},
        ]])
        self.assertEqual([c["id"] for c in result["conflicts"]], ["a", "b"])
        self.assertEqual(len(result["conflicts"][1]["sides"]), 3)


class ResumeTest(unittest.TestCase):
    def setUp(self):
        module._reset()
        self.tmp = tempfile.mkdtemp()

    def _rich(self, number, rows):
        return {"shard": number, "rows": rows}

    def test_checkpoint_file_named_by_batch(self):
        result = merge([self._rich(1, [{"id": 1, "v": 1}])],
                       batch="batch42", state_dir=self.tmp)
        path = result["token"]
        self.assertTrue(os.path.exists(path))
        self.assertEqual(os.path.basename(path), "checkpoint_batch42.json")

    def test_resume_from_breakpoint_skips_merged_shards(self):
        shards = [
            self._rich(2, [{"id": 2, "version": 1, "v": "s2"}]),
            self._rich(1, [{"id": 1, "version": 1, "v": "s1"}]),
        ]
        module._fail_after = 1
        with self.assertRaises(RuntimeError):
            merge(shards, batch="resume1", state_dir=self.tmp)
        module._fail_after = None

        token = glob.glob(os.path.join(self.tmp, "checkpoint_resume1.json"))[0]
        progress = resume(token)
        self.assertEqual(progress["done"], 1)
        manifest = progress["checkpoint"]["manifest"]
        self.assertEqual([entry["shard"] for entry in manifest], [1, 2])  # 清单按分片号排序

        result = merge(shards, batch="resume1", state_dir=self.tmp)
        self.assertEqual(result["processed"], 1)  # 只处理剩余的一个分片
        self.assertEqual(result["rows"], 2)
        self.assertEqual(resume(result["token"])["done"], 2)

    def test_duplicate_shard_is_idempotent(self):
        shards = [
            self._rich(1, [{"id": 1, "version": 1, "v": "a"}]),
            self._rich(2, [
                {"id": 1, "version": 2, "v": "b"},
                {"id": 2, "version": 1, "v": "c"},
            ]),
        ]
        first = merge(shards, batch="idem", state_dir=self.tmp)
        second = merge(shards, batch="idem", state_dir=self.tmp)
        self.assertEqual(second["processed"], 0)
        self.assertEqual(second["rows"], first["rows"])
        self.assertEqual(second["conflicts"], first["conflicts"])
        self.assertEqual(module._rows[1]["v"], "b")
        self.assertEqual(module._rows[2]["v"], "c")
        self.assertEqual(second["checkpoint"]["manifest"], first["checkpoint"]["manifest"])

    def test_checkpoint_records_position_within_shard(self):
        shards = [self._rich(1, [
            {"id": 1, "v": 1}, {"id": 2, "v": 2}, {"id": 3, "v": 3},
        ])]
        module._fail_after = 2
        with self.assertRaises(RuntimeError):
            merge(shards, batch="pos", state_dir=self.tmp)
        module._fail_after = None
        token = glob.glob(os.path.join(self.tmp, "checkpoint_pos.json"))[0]
        entry = resume(token)["checkpoint"]["manifest"][0]
        self.assertEqual(entry["position"], 2)
        self.assertEqual(entry["rows"], 3)

        result = merge(shards, batch="pos", state_dir=self.tmp)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["rows"], 3)


if __name__ == "__main__":
    unittest.main()
