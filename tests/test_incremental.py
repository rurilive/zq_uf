import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import importlib
from fileindex import CHUNK_SIZE, FileIndexError, changed, index, reset

module = importlib.import_module("fileindex.index")


class IncrementalIndexTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.root = tempfile.mkdtemp()
        self.a_path = os.path.join(self.root, "a.parquet")
        self.b_path = os.path.join(self.root, "b.parquet")
        with open(self.a_path, "w") as handle:
            handle.write("AAA")
        with open(self.b_path, "w") as handle:
            handle.write("BBB")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _abs(self, name):
        return os.path.abspath(os.path.join(self.root, name))

    def test_first_scan_counts_added(self):
        stats = index(self.root)
        self.assertEqual(stats["added"], 2)
        self.assertEqual(stats["changed"], 0)
        self.assertEqual(stats["deleted"], 0)
        self.assertEqual(stats["skipped"], 0)
        self.assertEqual(stats["indexed"], 2)
        self.assertEqual(stats["total"], 2)

    def test_unchanged_rescan_skips_everything(self):
        index(self.root)
        stats = index(self.root)
        self.assertEqual(stats["added"], 0)
        self.assertEqual(stats["changed"], 0)
        self.assertEqual(stats["deleted"], 0)
        self.assertEqual(stats["skipped"], 2)
        self.assertEqual(stats["indexed"], 2)
        self.assertEqual(stats["total"], 2)
        self.assertFalse(changed(self.a_path))

    def test_mtime_only_change_same_content_is_skipped(self):
        index(self.root)
        stamp = time.time() + 10
        os.utime(self.a_path, (stamp, stamp))
        self.assertTrue(changed(self.a_path))
        stats = index(self.root)
        self.assertEqual(stats["skipped"], 2)
        self.assertEqual(stats["changed"], 0)
        self.assertFalse(changed(self.a_path))

    def test_same_size_content_change_is_detected(self):
        index(self.root)
        stamp = time.time() + 10
        with open(self.a_path, "w") as handle:
            handle.write("ZZZ")
        os.utime(self.a_path, (stamp, stamp))
        self.assertTrue(changed(self.a_path))
        stats = index(self.root)
        self.assertEqual(stats["changed"], 1)
        self.assertEqual(stats["skipped"], 1)
        self.assertFalse(changed(self.a_path))

    def test_size_change_is_detected(self):
        index(self.root)
        stamp = time.time() + 10
        with open(self.a_path, "w") as handle:
            handle.write("AAAA")
        os.utime(self.a_path, (stamp, stamp))
        stats = index(self.root)
        self.assertEqual(stats["changed"], 1)
        self.assertEqual(stats["skipped"], 1)

    def test_deleted_file_is_cleaned_and_counted(self):
        index(self.root)
        os.remove(self.b_path)
        stats = index(self.root)
        self.assertEqual(stats["deleted"], 1)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(stats["total"], 1)
        self.assertNotIn(self._abs("b.parquet"), module._index)

    def test_added_file_is_counted_on_rescan(self):
        index(self.root)
        with open(os.path.join(self.root, "new.parquet"), "w") as handle:
            handle.write("NNN")
        stats = index(self.root)
        self.assertEqual(stats["added"], 1)
        self.assertEqual(stats["skipped"], 2)
        self.assertEqual(stats["total"], 3)

    def test_mixed_rescan_stats(self):
        gone_path = os.path.join(self.root, "gone.parquet")
        with open(gone_path, "w") as handle:
            handle.write("GGG")
        first = index(self.root)
        self.assertEqual(first["added"], 3)

        stamp = time.time() + 10
        os.utime(self.a_path, (stamp, stamp))
        with open(self.b_path, "w") as handle:
            handle.write("XXX")
        os.remove(gone_path)
        with open(os.path.join(self.root, "new.parquet"), "w") as handle:
            handle.write("NNN")

        second = index(self.root)
        self.assertEqual(second["added"], 1)
        self.assertEqual(second["changed"], 1)
        self.assertEqual(second["deleted"], 1)
        self.assertEqual(second["skipped"], 1)
        self.assertEqual(second["indexed"], 3)
        self.assertEqual(second["total"], 3)

    def test_subdirectory_incremental_keeps_outside_records(self):
        sub_dir = os.path.join(self.root, "sub")
        os.mkdir(sub_dir)
        sub_path = os.path.join(sub_dir, "s.parquet")
        with open(sub_path, "w") as handle:
            handle.write("SSS")
        index(self.root)

        os.remove(sub_path)
        stats = index(sub_dir)
        self.assertEqual(stats["deleted"], 1)
        self.assertEqual(stats["total"], 2)
        self.assertNotIn(os.path.abspath(sub_path), module._index)
        self.assertIn(self._abs("a.parquet"), module._index)

    def test_empty_file_has_single_empty_chunk(self):
        empty_path = os.path.join(self.root, "empty.parquet")
        open(empty_path, "w").close()
        index(self.root)
        entry = module._index[os.path.abspath(empty_path)]
        self.assertEqual(entry["size"], 0)
        self.assertEqual(len(entry["chunks"]), 1)
        self.assertEqual(entry["chunks"][0], module.hashlib.sha256(b"").hexdigest())

    def test_chunk_boundary_block_count(self):
        exact_path = os.path.join(self.root, "exact.bin")
        overflow_path = os.path.join(self.root, "overflow.bin")
        with open(exact_path, "wb") as handle:
            handle.write(b"x" * CHUNK_SIZE)
        with open(overflow_path, "wb") as handle:
            handle.write(b"x" * (CHUNK_SIZE + 1))
        index(self.root)
        self.assertEqual(len(module._index[os.path.abspath(exact_path)]["chunks"]), 1)
        self.assertEqual(len(module._index[os.path.abspath(overflow_path)]["chunks"]), 2)

    def test_chunk_difference_same_size_triggers_change(self):
        target = os.path.join(self.root, "target.bin")
        with open(target, "wb") as handle:
            handle.write(b"a" * (CHUNK_SIZE + 8))
        index(self.root)
        stamp = time.time() + 10
        with open(target, "wb") as handle:
            handle.write(b"a" * CHUNK_SIZE + b"b" * 8)
        os.utime(target, (stamp, stamp))
        stats = index(self.root)
        self.assertEqual(stats["changed"], 1)


class ChangedQueryTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.root = tempfile.mkdtemp()
        self.path = os.path.join(self.root, "a.parquet")
        with open(self.path, "w") as handle:
            handle.write("AAA")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_unindexed_file_reports_changed(self):
        self.assertTrue(changed(self.path))

    def test_missing_file_raises_clear_error(self):
        index(self.root)
        os.remove(self.path)
        with self.assertRaises(FileIndexError) as ctx:
            changed(self.path)
        self.assertIn("文件不存在", str(ctx.exception))


class ErrorPathTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_index_missing_root_raises(self):
        missing = os.path.join(self.root, "nope")
        with self.assertRaises(FileIndexError) as ctx:
            index(missing)
        self.assertIn("不存在", str(ctx.exception))

    def test_index_file_root_raises(self):
        file_path = os.path.join(self.root, "a.parquet")
        with open(file_path, "w") as handle:
            handle.write("AAA")
        with self.assertRaises(FileIndexError) as ctx:
            index(file_path)
        self.assertIn("不是目录", str(ctx.exception))

    def test_walk_error_is_raised_not_skipped(self):
        def boom(_exc):
            raise FileIndexError("遍历目录失败: boom")

        with mock.patch.object(module.os, "walk", side_effect=lambda *a, **k: boom(PermissionError("boom"))):
            with self.assertRaises(FileIndexError):
                index(self.root)

    @unittest.skipIf(os.geteuid() == 0, "root 绕过文件权限，无法模拟读取失败")
    def test_unreadable_file_raises_clear_error(self):
        path = os.path.join(self.root, "secret.parquet")
        with open(path, "w") as handle:
            handle.write("AAA")
        os.chmod(path, 0)
        try:
            with self.assertRaises(FileIndexError) as ctx:
                index(self.root)
            self.assertIn("无法读取文件", str(ctx.exception))
        finally:
            os.chmod(path, 0o644)

    def test_stat_failure_raises_clear_error(self):
        path = os.path.join(self.root, "a.parquet")
        with open(path, "w") as handle:
            handle.write("AAA")
        original_stat = module.os.stat

        def fake_stat(target, *args, **kwargs):
            if os.path.abspath(target) == os.path.abspath(path):
                raise OSError("stat boom")
            return original_stat(target, *args, **kwargs)

        with mock.patch.object(module.os, "stat", side_effect=fake_stat):
            with self.assertRaises(FileIndexError) as ctx:
                index(self.root)
            self.assertIn("无法获取文件状态", str(ctx.exception))


class DemoContractTest(unittest.TestCase):
    def test_demo_two_scans_match_documented_stats(self):
        import demo

        first, second = demo.main()
        self.assertEqual(first, {
            "added": 3, "changed": 0, "deleted": 0, "skipped": 0,
            "indexed": 3, "total": 3,
        })
        self.assertEqual(second, {
            "added": 1, "changed": 1, "deleted": 1, "skipped": 1,
            "indexed": 3, "total": 3,
        })


if __name__ == "__main__":
    unittest.main()
