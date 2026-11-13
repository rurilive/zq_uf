import unittest

from crawldiagnose import capture, diagnose


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
