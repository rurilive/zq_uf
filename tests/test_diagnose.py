import unittest

from crawldiagnose import capture, diagnose
from crawldiagnose.diagnose import REPLAY_TIME


class DiagnoseTest(unittest.TestCase):
    def test_capture_returns_id(self):
        cid = capture({"method": "GET", "url": "http://shop/1", "status": 500, "response": "err"})
        self.assertTrue(cid)

    def test_headers_and_body_dropped(self):
        cid = capture({"method": "POST", "url": "http://shop/login",
                       "headers": {"Cookie": "sid=1"}, "body": "u=1", "status": 200})
        record = diagnose(cid)
        self.assertNotIn("headers", record)
        self.assertNotIn("body", record)

    def test_reads_back_same_response(self):
        cid = capture({"method": "GET", "url": "http://shop/1", "status": 200, "response": "ok"})
        self.assertEqual(diagnose(cid)["response"], "ok")

    def test_capture_keeps_full_request(self):
        cid = capture({"method": "POST", "url": "http://shop/login?ts=1&sign=abc",
                       "headers": {"Cookie": "sid=1", "X-App": "shop"},
                       "body": "u=1&token=t9", "status": 200, "response": "ok",
                       "timestamp": 123.0})
        record = diagnose(cid)
        self.assertEqual(record["captured_at"], 123.0)
        self.assertEqual(len(record["response_digest"]), 64)
        self.assertEqual(record["request"]["method"], "POST")
        self.assertEqual(record["request"]["headers"]["Cookie"], "<redacted>")
        self.assertEqual(record["request"]["headers"]["X-App"], "shop")
        self.assertIn("ts=%s" % REPLAY_TIME, record["request"]["url"])

    def test_replay_is_deterministic(self):
        cid = capture({"method": "GET",
                       "url": "http://shop/1?ts=1&nonce=x&sign=abc",
                       "headers": {"Cookie": "sid=1"}, "status": 200, "response": "ok",
                       "replay": {"request": {"url": "http://shop/1?ts=2&nonce=y&sign=def",
                                              "headers": {"Cookie": "sid=2"}},
                                  "response": "ok2"}})
        self.assertEqual(diagnose(cid), diagnose(cid))

    def test_sensitive_replacement_not_flagged_as_change(self):
        cid = capture({"method": "GET", "url": "http://shop/1?sign=aaa&ts=111",
                       "headers": {"Cookie": "sid=1"}, "status": 200, "response": "ok",
                       "replay": {"request": {"url": "http://shop/1?sign=bbb&ts=222",
                                              "headers": {"Cookie": "sid=2"}}}})
        self.assertEqual(diagnose(cid)["category"], "consistent")

    def test_response_changed_when_request_same(self):
        cid = capture({"method": "GET", "url": "http://shop/1", "status": 200,
                       "response": "old", "replay": {"response": "new"}})
        record = diagnose(cid)
        self.assertEqual(record["category"], "response_changed")
        self.assertFalse(record["request_diff"])
        self.assertIsNone(record["status_diff"])
        self.assertTrue(record["response_diff"])

    def test_request_changed(self):
        cid = capture({"method": "POST", "url": "http://shop/pay", "body": "a=1",
                       "status": 200, "response": "ok",
                       "replay": {"request": {"body": "a=1&b=2"}}})
        record = diagnose(cid)
        self.assertEqual(record["category"], "request_changed")
        self.assertIn("body", record["request_diff"])

    def test_status_changed(self):
        cid = capture({"method": "GET", "url": "http://shop/1", "status": 200,
                       "response": "ok", "replay": {"status": 403}})
        record = diagnose(cid)
        self.assertEqual(record["category"], "status_changed")
        self.assertEqual(record["status_diff"], {"capture": 200, "replay": 403})

    def test_diff_reports_changed_fields_and_hints(self):
        cid = capture({"method": "GET", "url": "http://shop/1?page=1",
                       "headers": {"X-App": "shop"}, "status": 200, "response": "ok",
                       "replay": {"request": {"url": "http://shop/1?page=2",
                                              "headers": {"X-App": "shop", "X-Sign": "v2"}},
                                  "response": "ok2"}})
        record = diagnose(cid)
        self.assertIn("url", record["request_diff"])
        self.assertIn("X-Sign", record["request_diff"]["headers"])
        self.assertTrue(record["hints"])
