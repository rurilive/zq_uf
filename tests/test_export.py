import os
import shutil
import tempfile
import unittest

from auditexport import export, resume


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_export_rows(self):
        path = os.path.join(self.dir, "out.jsonl")
        self.assertEqual(export({"records": [{"id": 1}, {"id": 2}]}, path)["rows"], 2)

    def test_export_writes_file(self):
        path = os.path.join(self.dir, "out.jsonl")
        export({"records": [{"id": 1}]}, path)
        self.assertEqual(len(open(path, encoding="utf-8").readlines()), 1)

    def test_resume_unsupported(self):
        with self.assertRaises(NotImplementedError):
            resume("t")
