import json
import os
import tempfile
import unittest

from trafficmask import find, mask_request, record, replay


class MaskRuleTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "traffic.jsonl")

    def _raw(self):
        with open(self.path, encoding="utf-8") as handle:
            return handle.read()

    def test_cookie_masked_keep_length_and_affix(self):
        req = {"method": "GET", "url": "http://shop/1",
               "headers": {"Cookie": "sid=abcdefgh; uid=12345"}}
        record(req, path=self.path, mask=True)
        entry = json.loads(self._raw().splitlines()[0])
        cookie = entry["request"]["headers"]["Cookie"]
        self.assertEqual(cookie, "sid=ab****gh; uid=12*45")
        self.assertEqual(len(cookie), len("sid=abcdefgh; uid=12345"))
        self.assertNotIn("abcdefgh", self._raw())

    def test_token_masked_keep_length_and_affix(self):
        req = {"method": "POST", "url": "http://shop/login",
               "headers": {"Authorization": "Bearer abcdef123456"},
               "body": {"token": "tok9876543"}}
        record(req, path=self.path, mask=True)
        entry = json.loads(self._raw().splitlines()[0])
        auth = entry["request"]["headers"]["Authorization"]
        self.assertEqual(auth, "Bearer ab********56")
        self.assertEqual(len(auth), len("Bearer abcdef123456"))
        self.assertEqual(entry["request"]["body"]["token"], "to******43")
        self.assertNotIn("abcdef123456", self._raw())

    def test_phone_masked_keep_length_and_affix(self):
        req = {"method": "POST", "url": "http://shop/u?phone=13912345678",
               "body": "phone=13800000000"}
        record(req, path=self.path, mask=True)
        entry = json.loads(self._raw().splitlines()[0])
        self.assertEqual(entry["request"]["body"], "phone=138******00")
        self.assertEqual(entry["request"]["url"], "http://shop/u?phone=139******78")
        self.assertNotIn("13800000000", self._raw())

    def test_short_value_fully_masked(self):
        masked = mask_request({"headers": {"Cookie": "sid=abc"}})
        self.assertEqual(masked["headers"]["Cookie"], "sid=***")

    def test_mask_does_not_mutate_original(self):
        req = {"headers": {"Cookie": "sid=abcdefgh"}}
        mask_request(req)
        self.assertEqual(req["headers"]["Cookie"], "sid=abcdefgh")

    def test_masked_roundtrip_deterministic(self):
        req = {"method": "GET", "url": "http://shop/1",
               "headers": {"Cookie": "sid=abcdefgh"}}
        record(req, path=self.path, batch="b1", mask=True)
        record(req, path=self.path, batch="b1", mask=True)
        first, second = replay(self.path, drill_path=False)
        self.assertEqual(first, second)
        self.assertEqual(first["headers"]["Cookie"], "sid=ab****gh")


class ReplayRebuildTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "traffic.jsonl")

    def test_rebuild_method_headers_body(self):
        req = {"method": "POST", "url": "http://shop/login",
               "headers": {"X-Token": "tok123456"}, "body": "a=1"}
        record(req, path=self.path, mask=True)
        (rebuilt,) = replay(self.path, drill_path=False)
        self.assertEqual(rebuilt["method"], "POST")
        self.assertEqual(rebuilt["headers"], {"X-Token": "to*****56"})
        self.assertEqual(rebuilt["body"], "a=1")

    def test_corrupt_line_raises(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("not-json\n")
        with self.assertRaises(ValueError):
            replay(self.path, drill_path=False)

    def test_record_rejects_non_dict(self):
        with self.assertRaises(TypeError):
            record("not-a-dict", path=self.path)


class CrossFileFindTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.day1 = os.path.join(self.dir, "traffic-2026-11-13.jsonl")
        self.day2 = os.path.join(self.dir, "traffic-2026-11-14.jsonl")

    def test_find_across_files_by_batch(self):
        record({"method": "POST", "url": "http://shop/login"},
               path=self.day1, batch="case-7")
        record({"method": "GET", "url": "http://shop/order"},
               path=self.day2, batch="case-7")
        record({"method": "GET", "url": "http://shop/other"},
               path=self.day2, batch="case-8")
        hits = find("case-7", [self.day1, self.day2])
        self.assertEqual([r["url"] for r in hits],
                         ["http://shop/login", "http://shop/order"])

    def test_find_accepts_directory(self):
        record({"method": "GET", "url": "http://shop/1"},
               path=self.day1, batch="case-9")
        self.assertEqual(len(find("case-9", self.dir)), 1)

    def test_find_missing_path_raises(self):
        with self.assertRaises(FileNotFoundError):
            find("case-7", [os.path.join(self.dir, "none.jsonl")])


class DrillIsolationTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "traffic.jsonl")
        self.drill = os.path.join(self.dir, "drill.jsonl")

    def test_replay_writes_isolated_drill_store(self):
        req = {"method": "GET", "url": "http://shop/1"}
        record(req, path=self.path)
        with open(self.path, encoding="utf-8") as handle:
            before = handle.read()
        replay(self.path, drill_path=self.drill)
        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), before)
        with open(self.drill, encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["request"], req)
        self.assertEqual(rows[0]["source"], os.path.abspath(self.path))

    def test_default_drill_path_is_sibling(self):
        record({"method": "GET", "url": "http://shop/1"}, path=self.path)
        replay(self.path)
        expected = os.path.join(self.dir, "traffic.drill.jsonl")
        self.assertTrue(os.path.exists(expected))

    def test_drill_disabled(self):
        record({"method": "GET", "url": "http://shop/1"}, path=self.path)
        replay(self.path, drill_path=False)
        self.assertFalse(
            os.path.exists(os.path.join(self.dir, "traffic.drill.jsonl")))


if __name__ == "__main__":
    unittest.main()

