import os
import tempfile
import unittest

from auditlog import record, replay


class AuditlogTest(unittest.TestCase):
    def test_record_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "audit.jsonl")
            record({"ok": 2, "failed": 1}, path)
            self.assertEqual(replay(path), {"fixed": 2, "failed": 1})

    def test_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "audit.jsonl")
            open(path, "w").close()
            self.assertEqual(replay(path), {"fixed": 0, "failed": 0})
