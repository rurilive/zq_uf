import json
import os
import shutil
import tempfile
import unittest

from partquery import ROOT, query


class QueryTest(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.dir = tempfile.mkdtemp()
        os.chdir(self.dir)
        base = os.path.join(ROOT, "orders")
        os.makedirs(base)
        for day, city in (("2026-01-01", "sh"), ("2026-01-02", "bj")):
            with open(os.path.join(base, f"dt={day}.jsonl"), "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"city": city, "amount": 1}) + "\n")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_filters_rows(self):
        self.assertEqual(query("orders", {"city": "bj"})["rows"][0]["city"], "bj")

    def test_scans_every_partition(self):
        self.assertEqual(query("orders", {"city": "sh"})["scanned"], 2)

    def test_missing_dataset_silent(self):
        self.assertEqual(query("nope", {})["rows"], [])
