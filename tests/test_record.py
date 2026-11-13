import json
import os
import tempfile
import unittest

from trafficmask import record, replay


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "traffic.jsonl")

    def test_roundtrip(self):
        req = {"method": "GET", "url": "http://shop/1", "headers": {"Cookie": "sid=abc"}}
        record(req, path=self.path)
        self.assertEqual(replay(self.path), [req])

    def test_secrets_kept_plain(self):
        req = {"url": "http://shop/1", "headers": {"Cookie": "sid=abc"}, "body": "phone=13800000000"}
        record(req, path=self.path)
        raw = open(self.path, encoding="utf-8").read()
        self.assertIn("sid=abc", raw)
        self.assertIn("13800000000", raw)

    def test_missing_file_empty(self):
        self.assertEqual(replay(os.path.join(self.dir, "none.jsonl")), [])
