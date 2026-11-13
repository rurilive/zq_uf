import json
import os
import tempfile
import unittest

from harvestcfg import load


class HarvestcfgTest(unittest.TestCase):
    def test_load_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "site.json")
            open(path, "w", encoding="utf-8").write(json.dumps({"limit": 2}))
            self.assertEqual(load(path), {"limit": 2})

    def test_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            load("/nonexistent/site.json")
