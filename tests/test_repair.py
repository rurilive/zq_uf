import os
import shutil
import tempfile
import unittest

from csvrepair import repair

SCHEMA = ["id", "name", "price"]


class RepairTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "dirty.csv")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, text):
        open(self.path, "w", encoding="utf-8").write(text)

    def test_short_row_padded(self):
        self.write("1,keyboard\n")
        result = repair(self.path, SCHEMA)
        self.assertEqual(result["rows"][0], ["1", "keyboard", ""])

    def test_quoted_comma_split(self):
        self.write('2,"mouse,black",99\n')
        result = repair(self.path, SCHEMA)
        self.assertEqual(result["rows"][0][1], '"mouse')

    def test_no_report(self):
        self.write("1,keyboard,299\n")
        result = repair(self.path, SCHEMA)
        self.assertNotIn("report", result)
