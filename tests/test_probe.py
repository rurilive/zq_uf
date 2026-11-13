import unittest

from siteprobe import probe


class SiteprobeTest(unittest.TestCase):
    def test_probe_fields(self):
        result = probe("http://site/a", lambda url: {
            "status": 200,
            "body": "hello",
            "headers": {"content-type": "text/html"},
        })
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["bytes"], 5)
        self.assertEqual(result["content_type"], "text/html")

    def test_missing_response_fields(self):
        result = probe("http://site/a", lambda url: {})
        self.assertEqual(result["status"], 0)
        self.assertEqual(result["bytes"], 0)
