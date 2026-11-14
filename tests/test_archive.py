import io
import os
import shutil
import tempfile
import unittest

from attacharchive import reconcile, upload


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_upload_reads_all(self):
        self.assertEqual(upload("a.bin", io.BytesIO(b"abc"), self.dir)["size"], 3)

    def test_no_resume(self):
        self.assertNotIn("chunks", upload("a.bin", io.BytesIO(b"abc"), self.dir))

    def test_reconcile_always_clean(self):
        self.assertEqual(reconcile([{"key": "a.bin"}]), {"missing": [], "orphans": [], "corrupt": []})
