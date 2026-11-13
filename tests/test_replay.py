import os
import tempfile
import unittest

from replayfail import record, replay


class ReplayfailTest(unittest.TestCase):
    def test_record_writes_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.jsonl")
            record({"url": "http://a"}, path)
            self.assertEqual(len(open(path, encoding="utf-8").read().splitlines()), 1)

    def test_replay_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.jsonl")
            record({"url": "http://ok"}, path)
            record({"url": "http://bad"}, path)
            counts = replay([path], lambda url: url.endswith("ok"))
        self.assertEqual(counts, {"fixed": 1, "still_failed": 1})

    def test_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "f.jsonl")
            open(path, "w").close()
            self.assertEqual(replay([path], lambda url: True), {"fixed": 0, "still_failed": 0})
