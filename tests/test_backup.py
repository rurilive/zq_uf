import json
import os
import shutil
import tempfile
import unittest

from incbackup import (
    CHUNK_SIZE,
    MANIFEST_NAME,
    TAG_FILE,
    BackupError,
    RestoreError,
    backup,
    restore,
)


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.src = os.path.join(self.dir, "data")
        os.makedirs(self.src)
        open(os.path.join(self.src, "a.txt"), "w").write("AAA")
        self.dest = os.path.join(self.dir, "backups")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_full_copy(self):
        result = backup(self.src, self.dest)
        self.assertEqual(result["files"], 1)
        self.assertTrue(os.path.exists(os.path.join(result["path"], "a.txt")))

    def test_every_backup_copies_everything(self):
        first = backup(self.src, self.dest)
        second = backup(self.src, self.dest)
        self.assertEqual(first["files"], second["files"])

    def test_restore_whole_snapshot(self):
        tag = backup(self.src, self.dest)["tag"]
        target = os.path.join(self.dir, "restore")
        restore(tag, target, self.dest)
        self.assertEqual(open(os.path.join(target, "a.txt")).read(), "AAA")


class ContentHashIncrementalTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.src = os.path.join(self.dir, "data")
        os.makedirs(self.src)
        self.dest = os.path.join(self.dir, "backups")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, name, content, binary=False):
        path = os.path.join(self.src, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "wb" if binary else "w"
        with open(path, mode) as handle:
            handle.write(content)
        return path

    def _read(self, name, root=None):
        root = root or self.src
        with open(os.path.join(root, name), "rb") as handle:
            return handle.read()

    def _blocks(self):
        root = os.path.join(self.dest, "blocks")
        result = []
        for dirpath, _, files in os.walk(root):
            result.extend(os.path.join(dirpath, name) for name in files)
        return result

    def _manifest(self, tag):
        with open(os.path.join(self.dest, tag, MANIFEST_NAME), encoding="utf-8") as handle:
            return json.load(handle)

    def test_unchanged_backup_creates_no_new_blocks(self):
        self._write("a.txt", "AAA")
        first = backup(self.src, self.dest)
        second = backup(self.src, self.dest)
        self.assertEqual(first["new_blocks"], 1)
        self.assertEqual(second["new_blocks"], 0)
        self.assertEqual(second["reused_blocks"], 1)
        self.assertEqual(len(self._blocks()), 1)
        self.assertEqual(second["parent"], first["tag"])

    def test_identical_content_is_deduplicated_across_files(self):
        self._write("a.txt", "SAME")
        self._write("sub/b.txt", "SAME")
        result = backup(self.src, self.dest)
        self.assertEqual(result["files"], 2)
        self.assertEqual(result["new_blocks"], 1)
        self.assertEqual(result["reused_blocks"], 1)
        self.assertEqual(len(self._blocks()), 1)

    def test_multi_chunk_file_backs_up_only_changed_chunk(self):
        head = b"H" * CHUNK_SIZE
        middle = b"M" * CHUNK_SIZE
        tail = b"T" * 10
        self._write("big.bin", head + middle + tail, binary=True)
        first = backup(self.src, self.dest)
        self.assertEqual(first["new_blocks"], 3)
        self._write("big.bin", head + middle + b"X" * 10, binary=True)
        second = backup(self.src, self.dest)
        self.assertEqual(second["new_blocks"], 1)
        self.assertEqual(second["reused_blocks"], 2)
        self.assertEqual(len(self._blocks()), 4)

    def test_empty_file_and_empty_directory_are_supported(self):
        self._write("empty.txt", "")
        os.makedirs(os.path.join(self.src, "emptydir"))
        result = backup(self.src, self.dest)
        self.assertEqual(result["files"], 1)
        self.assertEqual(result["new_blocks"], 0)
        self.assertEqual(result["reused_blocks"], 0)
        target = os.path.join(self.dir, "restore")
        restored = restore(result["tag"], target, self.dest)
        self.assertEqual(restored["files"], 1)
        self.assertEqual(self._read("empty.txt", target), b"")
        self.assertTrue(os.path.isdir(os.path.join(target, "emptydir")))

    def test_restore_any_point_along_parent_chain(self):
        self._write("a.txt", "v1")
        first = backup(self.src, self.dest)
        self._write("a.txt", "v2-content")
        second = backup(self.src, self.dest)
        self._write("a.txt", "v3-content-now")
        third = backup(self.src, self.dest)
        self.assertEqual(self._manifest(third["tag"])["parent"], second["tag"])
        self.assertEqual(self._manifest(second["tag"])["parent"], first["tag"])
        self.assertIsNone(self._manifest(first["tag"])["parent"])

        first_target = os.path.join(self.dir, "r1")
        first_restore = restore(first["tag"], first_target, self.dest)
        self.assertEqual(first_restore["chain"], [first["tag"]])
        self.assertEqual(self._read("a.txt", first_target), b"v1")

        third_target = os.path.join(self.dir, "r3")
        third_restore = restore(third["tag"], third_target, self.dest)
        self.assertEqual(
            third_restore["chain"], [first["tag"], second["tag"], third["tag"]]
        )
        self.assertEqual(third_restore["files"], 1)
        self.assertEqual(self._read("a.txt", third_target), b"v3-content-now")

    def test_restore_rebuilds_nested_directories_and_content(self):
        self._write("d1/d2/a.txt", "AAA")
        self._write("d1/b.txt", "BBB")
        result = backup(self.src, self.dest)
        target = os.path.join(self.dir, "restore")
        restore(result["tag"], target, self.dest)
        self.assertEqual(self._read("d1/d2/a.txt", target), b"AAA")
        self.assertEqual(self._read("d1/b.txt", target), b"BBB")

    def test_broken_parent_chain_is_rejected(self):
        self._write("a.txt", "v1")
        first = backup(self.src, self.dest)
        self._write("a.txt", "v2")
        second = backup(self.src, self.dest)
        shutil.rmtree(os.path.join(self.dest, first["tag"]))
        target = os.path.join(self.dir, "restore")
        with self.assertRaises(RestoreError):
            restore(second["tag"], target, self.dest)
        self.assertFalse(os.path.exists(target))

    def test_missing_block_is_rejected_before_writing_restore(self):
        self._write("a.txt", "AAA")
        result = backup(self.src, self.dest)
        os.remove(self._blocks()[0])
        target = os.path.join(self.dir, "restore")
        with self.assertRaises(RestoreError):
            restore(result["tag"], target, self.dest)
        self.assertFalse(os.path.exists(target))

    def test_corrupted_block_is_rejected(self):
        self._write("a.txt", "AAA")
        result = backup(self.src, self.dest)
        block = self._blocks()[0]
        with open(block, "wb") as handle:
            handle.write(b"BBB")
        target = os.path.join(self.dir, "restore")
        with self.assertRaises(RestoreError):
            restore(result["tag"], target, self.dest)
        self.assertFalse(os.path.exists(target))

    def test_unknown_tag_and_missing_store_raise(self):
        self._write("a.txt", "AAA")
        backup(self.src, self.dest)
        with self.assertRaises(RestoreError):
            restore("snap-9999", os.path.join(self.dir, "r"), self.dest)
        with self.assertRaises(RestoreError):
            restore("snap-0001", os.path.join(self.dir, "r2"), os.path.join(self.dir, "nope"))

    def test_backup_rejects_missing_root_and_broken_tag_index(self):
        with self.assertRaises(BackupError):
            backup(os.path.join(self.dir, "missing"), self.dest)
        self._write("a.txt", "AAA")
        backup(self.src, self.dest)
        with open(os.path.join(self.dest, TAG_FILE), "w") as handle:
            handle.write("{not json")
        with self.assertRaises(BackupError):
            backup(self.src, self.dest)

    def test_backup_on_broken_chain_is_rejected(self):
        self._write("a.txt", "v1")
        first = backup(self.src, self.dest)
        shutil.rmtree(os.path.join(self.dest, first["tag"]))
        with self.assertRaises(BackupError):
            backup(self.src, self.dest)

    def test_symlink_is_recorded_and_restored(self):
        self._write("a.txt", "AAA")
        os.symlink("a.txt", os.path.join(self.src, "link"))
        result = backup(self.src, self.dest)
        self.assertEqual(result["files"], 2)
        target = os.path.join(self.dir, "restore")
        restore(result["tag"], target, self.dest)
        self.assertTrue(os.path.islink(os.path.join(target, "link")))
        self.assertEqual(os.readlink(os.path.join(target, "link")), "a.txt")

    def test_snapshot_view_shares_inode_with_block_store(self):
        self._write("a.txt", "AAA")
        result = backup(self.src, self.dest)
        block = self._blocks()[0]
        view_file = os.path.join(result["path"], "a.txt")
        self.assertEqual(os.stat(view_file).st_ino, os.stat(block).st_ino)

    def test_repeated_backups_do_not_grow_block_store_when_unchanged(self):
        self._write("a.txt", "AAA")
        backup(self.src, self.dest)
        size_after_first = sum(os.path.getsize(path) for path in self._blocks())
        for _ in range(3):
            backup(self.src, self.dest)
        size_after_repeats = sum(os.path.getsize(path) for path in self._blocks())
        self.assertEqual(size_after_first, size_after_repeats)
        self.assertEqual(len(self._blocks()), 1)
