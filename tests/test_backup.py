import os
import shutil
import tempfile
import unittest

from incbackup import backup, restore


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
