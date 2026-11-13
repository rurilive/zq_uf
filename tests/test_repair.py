import hashlib
import json
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

    def test_short_row_padded_in_repaired(self):
        self.write("1,keyboard\n")
        result = repair(self.path, SCHEMA)
        self.assertEqual(result["repaired"][0], ["1", "keyboard", ""])

    def test_quoted_comma_resplit(self):
        self.write('2,"mouse,black",99\n')
        result = repair(self.path, SCHEMA)
        self.assertEqual(result["repaired"][0], ["2", "mouse,black", "99"])

    def test_embedded_newline_joined(self):
        self.write('4,"wireless\nmouse",10\n')
        result = repair(self.path, SCHEMA)
        self.assertEqual(result["repaired"][0], ["4", "wireless\nmouse", "10"])

    def test_unclosed_quote_kept_raw(self):
        self.write('5,"unclosed,1\n')
        result = repair(self.path, SCHEMA)
        self.assertEqual(result["repaired"][0], '5,"unclosed,1')
        out = open(result["out"], encoding="utf-8").read()
        self.assertEqual(out, '5,"unclosed,1\n')

    def test_column_overflow_kept_raw(self):
        self.write("6,a,b,c,d\n")
        result = repair(self.path, SCHEMA)
        self.assertEqual(result["repaired"][0], "6,a,b,c,d")

    def test_report_records_raw_and_actions(self):
        self.write('2,"mouse,black",99\n5,"unclosed,1\n')
        result = repair(self.path, SCHEMA)
        report = json.load(open(result["report_path"], encoding="utf-8"))
        actions = {entry["line"]: entry for entry in report["actions"]}
        self.assertEqual(actions[1]["action"], "resplit")
        self.assertEqual(actions[1]["raw"], '2,"mouse,black",99')
        self.assertEqual(actions[2]["action"], "kept_raw")
        self.assertEqual(actions[2]["reason"], "unclosed_quote")
        self.assertEqual(report["output_rows"], 2)

    def test_checksum_matches_output(self):
        self.write("1,keyboard,299\n")
        result = repair(self.path, SCHEMA)
        digest = hashlib.sha256(
            open(result["out"], "rb").read()).hexdigest()
        self.assertEqual(result["checksum"], digest)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            repair(os.path.join(self.dir, "nope.csv"), SCHEMA)

    def test_empty_schema_raises(self):
        self.write("1,keyboard,299\n")
        with self.assertRaises(ValueError):
            repair(self.path, [])
