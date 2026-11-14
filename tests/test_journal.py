import os
import shutil
import tempfile
import unittest

from crawljournal import append, recover


class JournalTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "journal.log")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_append_recover(self):
        append({"id": 1}, self.path)
        append({"id": 2}, self.path)
        self.assertEqual([r["id"] for r in recover(self.path)], [1, 2])

    def test_half_line_raises(self):
        append({"id": 1}, self.path)
        open(self.path, "a", encoding="utf-8").write('{"id": 2')
        with self.assertRaises(ValueError):
            recover(self.path)

    def test_no_checksum(self):
        append({"id": 1}, self.path)
        self.assertNotIn("checksum", recover(self.path)[0])
