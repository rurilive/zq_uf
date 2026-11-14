import json
import os
import shutil
import tempfile
import unittest

from partcompact import compact, query


class CompactTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for i in range(2):
            with open(os.path.join(self.dir, f"part{i}.jsonl"), "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"id": i}) + "\n")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_merges_files(self):
        compact(self.dir, [{"id": 1}, {"id": 2}])
        self.assertEqual(len(query(self.dir)), 2)

    def test_old_files_deleted_first(self):
        compact(self.dir, [{"id": 1}])
        self.assertEqual([n for n in os.listdir(self.dir) if n.startswith("part")], [])

    def test_no_manifest(self):
        self.assertNotIn("manifest", {"path": compact(self.dir, [])})
