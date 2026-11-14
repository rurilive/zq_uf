import importlib
import json
import os
import shutil
import tempfile
import unittest

from tablearchive import archive, last_route, query, restore

module = importlib.import_module("tablearchive.archive")


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        module._rows.clear()

    def test_archive_zero(self):
        self.assertEqual(archive("t", {"before": 2020})["archived"], 0)

    def test_query_filters(self):
        module._rows.extend([{"city": "sh"}, {"city": "bj"}])
        self.assertEqual(len(query("t", {"city": "sh"})), 1)

    def test_query_empty(self):
        self.assertEqual(query("t", {}), [])


class TieredTest(unittest.TestCase):
    """冷热分层：归档、透明路由、合并顺序、恢复校验。"""

    def setUp(self):
        module._rows.clear()
        module._meta.clear()
        module._order.clear()
        module._seq = 0
        self._old_dir = module._cold_dir
        self._tmp = tempfile.mkdtemp()
        module._cold_dir = self._tmp

    def tearDown(self):
        module._cold_dir = self._old_dir
        module._meta.clear()
        module._order.clear()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _seed(self):
        module._rows.extend(
            {"city": city, "year": year, "v": i}
            for i, (year, city) in enumerate(
                [(2021, "sh"), (2022, "bj"), (2023, "sh"), (2024, "bj")]
            )
        )

    def test_archive_moves_rows_and_records_meta(self):
        self._seed()
        result = archive("t", {"before": 2023})
        self.assertEqual(result["archived"], 2)
        self.assertEqual(result["range"], [2021, 2022])
        self.assertTrue(os.path.exists(result["path"]))
        # 源分区已删除，热层只剩 2023/2024
        self.assertEqual([r["year"] for r in module._rows], [2023, 2024])
        # 元数据记录分区位置和时间范围
        self.assertEqual(len(module._meta), 1)
        meta = module._meta[0]
        self.assertEqual(meta["partition"], result["partition"])
        self.assertEqual(meta["path"], result["path"])
        self.assertEqual((meta["start"], meta["end"]), (2021, 2022))
        self.assertEqual(meta["count"], 2)
        self.assertTrue(os.path.exists(os.path.join(self._tmp, "_meta.json")))

    def test_query_routes_hot_and_cold_transparently(self):
        self._seed()
        archive("t", {"before": 2023})
        # 接口不变，结果与归档前一致
        self.assertEqual(len(query("t", {})), 4)
        self.assertEqual(len(query("t", {"city": "sh"})), 2)
        route = last_route()
        self.assertEqual(route["cold"], ["t_year_2021_2022"])
        self.assertEqual(route["merged"], 2)

    def test_query_prunes_cold_partitions_by_time_filter(self):
        self._seed()
        archive("t", {"before": 2023})
        rows = query("t", {"year": 2024})
        self.assertEqual([r["v"] for r in rows], [3])
        route = last_route()
        self.assertEqual(route["cold"], [])
        self.assertEqual(route["pruned"], ["t_year_2021_2022"])
        rows = query("t", {"year": 2021})
        self.assertEqual([r["v"] for r in rows], [0])
        self.assertEqual(last_route()["cold"], ["t_year_2021_2022"])

    def test_merge_preserves_original_order(self):
        self._seed()
        archive("t", {"before": 2023})
        module._rows.append({"city": "sh", "year": 2022, "v": 4})  # 归档后新写入
        self.assertEqual([r["v"] for r in query("t", {})], [0, 1, 2, 3, 4])

    def test_restore_moves_back_with_verification(self):
        self._seed()
        result = archive("t", {"before": 2023})
        restored = restore("t")
        self.assertTrue(restored["verified"])
        self.assertEqual(restored["restored"], 2)
        self.assertEqual(restored["partitions"], [result["partition"]])
        # 冷分区文件与元数据已清理，行回到热层且顺序不变
        self.assertFalse(os.path.exists(result["path"]))
        self.assertEqual(module._meta, [])
        self.assertEqual([r["v"] for r in query("t", {})], [0, 1, 2, 3])

    def test_restore_rejects_tampered_partition(self):
        self._seed()
        result = archive("t", {"before": 2023})
        with open(result["path"], "w", encoding="utf-8") as fh:
            fh.write(json.dumps([0, {"city": "sh", "year": 2021, "v": 99}]) + "\n")
        restored = restore("t")
        self.assertFalse(restored["verified"])
        self.assertEqual(restored["restored"], 0)
        # 校验失败不回迁，热层与元数据保持原样
        self.assertEqual(len(module._rows), 2)
        self.assertEqual(len(module._meta), 1)
