import hashlib
import json
import os
import shutil
import tempfile
import unittest

from csvrepair import RepairError, repair

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


class RepairFixTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "dirty.csv")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, text):
        open(self.path, "w", encoding="utf-8").write(text)

    def test_ragged_columns_padded_or_kept(self):
        self.write("1,keyboard\n2,mouse,99,extra\n")
        result = repair(self.path, SCHEMA)
        self.assertEqual(result["repaired"][0], ["1", "keyboard", ""])
        self.assertEqual(result["actions"], ["padded", "kept"])
        lines = open(result["out"], encoding="utf-8").read().splitlines()
        self.assertEqual(lines[1], "2,mouse,99,extra")

    def test_unclosed_quote_resplit(self):
        self.write('3,"key board,49\n')
        result = repair(self.path, SCHEMA)
        self.assertEqual(result["repaired"][0], ["3", '"key board', "49"])
        self.assertEqual(result["actions"], ["resplit"])

    def test_embedded_newline_joined(self):
        self.write('1,"key\nboard",299\n')
        result = repair(self.path, SCHEMA)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["repaired"][0], ["1", "key\nboard", "299"])
        self.assertEqual(result["actions"], ["joined"])

    def test_unrepairable_kept_and_reported(self):
        self.write("5,a,b,c,d\n")
        result = repair(self.path, SCHEMA)
        self.assertEqual(result["repaired"][0], "5,a,b,c,d")
        report = json.load(open(result["report_path"], encoding="utf-8"))
        self.assertEqual(report["records"][0]["raw"], "5,a,b,c,d")
        self.assertEqual(report["records"][0]["action"], "kept")
        self.assertEqual(report["checksum"], result["checksum"])
        self.assertEqual(report["total"], result["count"])

    def test_checksum_matches_output(self):
        self.write("1,keyboard,299\n")
        result = repair(self.path, SCHEMA)
        digest = hashlib.sha256(open(result["out"], "rb").read()).hexdigest()
        self.assertEqual(result["checksum"], digest)

    def test_missing_file_raises(self):
        with self.assertRaises(RepairError):
            repair(os.path.join(self.dir, "nope.csv"), SCHEMA)

    def test_empty_schema_raises(self):
        self.write("1,keyboard,299\n")
        with self.assertRaises(RepairError):
            repair(self.path, [])
