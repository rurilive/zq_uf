import os
import shutil
import tempfile
import unittest

from auditchain import append, verify
import json

from auditchain import export_report, rotate


class ChainTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "audit.log")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_append(self):
        append({"id": 1}, self.path)
        self.assertEqual(len(open(self.path, encoding="utf-8").readlines()), 1)

    def test_verify_always_true(self):
        append({"id": 1}, self.path)
        self.assertTrue(verify(path=self.path)["ok"])

    def test_no_hash_chain(self):
        append({"id": 1}, self.path)
        self.assertNotIn("prev_hash", open(self.path, encoding="utf-8").read())

    def _rewrite(self, index, mutate):
        with open(self.path, encoding="utf-8") as handle:
            lines = handle.readlines()
        record = json.loads(lines[index])
        mutate(record)
        lines[index] = json.dumps(record, ensure_ascii=False) + "\n"
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.writelines(lines)

    def test_tamper_detected(self):
        for i in range(1, 4):
            append({"id": i}, self.path)
        self._rewrite(1, lambda rec: rec["event"].update({"id": 99}))
        result = verify(path=self.path)
        self.assertFalse(result["ok"])
        self.assertEqual(result["break"]["seq"], 2)
        self.assertEqual(result["chain_length"], 3)

    def test_cross_file_verify(self):
        for i in range(1, 4):
            append({"id": i}, self.path)
        rotate(self.path)
        append({"id": 4}, self.path)
        append({"id": 5}, self.path)
        result = verify(path=self.path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["chain_length"], 5)
        with open(self.path + ".manifest.json", encoding="utf-8") as handle:
            segment = json.load(handle)["segments"][0]
        self.assertEqual((segment["start"], segment["end"]), (1, 3))
        self.assertTrue(segment["first"] and segment["last"])

    def test_cross_file_break(self):
        append({"id": 1}, self.path)
        rotate(self.path)
        append({"id": 2}, self.path)
        self._rewrite(0, lambda rec: rec.update({"prev": "0" * 64}))
        result = verify(path=self.path)
        self.assertFalse(result["ok"])
        self.assertEqual(result["break"]["seq"], 2)

    def test_range_verify(self):
        for i in range(1, 7):
            append({"id": i}, self.path)
        result = verify(start=2, end=4, path=self.path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 3)
        self.assertEqual(result["range"], [2, 4])
        self.assertEqual(result["chain_length"], 6)

    def test_range_verify_tamper_scope(self):
        for i in range(1, 7):
            append({"id": i}, self.path)
        self._rewrite(2, lambda rec: rec["event"].update({"id": 99}))
        self.assertFalse(verify(start=2, end=4, path=self.path)["ok"])
        self.assertTrue(verify(start=4, end=6, path=self.path)["ok"])

    def test_break_location_and_reason(self):
        for i in range(1, 4):
            append({"id": i}, self.path)
        self._rewrite(2, lambda rec: rec.update({"prev": "f" * 64}))
        result = verify(path=self.path)
        self.assertFalse(result["ok"])
        self.assertEqual(result["break"]["seq"], 3)
        self.assertIn("prev", result["break"]["reason"])

    def test_max_bytes_rotation(self):
        append({"id": 1}, self.path, max_bytes=10)
        append({"id": 2}, self.path, max_bytes=10)
        result = verify(path=self.path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["chain_length"], 2)

    def test_export_report(self):
        append({"id": 1}, self.path)
        result = verify(path=self.path)
        report_path = os.path.join(self.dir, "report.json")
        export_report(result, report_path)
        with open(report_path, encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertTrue(report["ok"])
        self.assertEqual(report["chain_length"], 1)
