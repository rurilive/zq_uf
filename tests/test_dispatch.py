import unittest

from contenttype import Failure, dispatch, parse


class DispatchTest(unittest.TestCase):
    def test_json_suffix(self):
        self.assertEqual(dispatch({"url": "http://a/x.json"}), "json")

    def test_html_default(self):
        self.assertEqual(dispatch({"url": "http://a/x"}), "html")

    def test_parse_text(self):
        self.assertEqual(parse({"body": "x"}), "x")


class MissingHeaderTest(unittest.TestCase):
    def test_json_without_header(self):
        r = dispatch({"url": "http://a/api", "body": '{"a": 1}'})
        self.assertEqual(r, "json")
        self.assertGreaterEqual(r.confidence, 0.8)

    def test_xml_without_header(self):
        r = dispatch({"url": "http://a/feed", "body": '<?xml version="1.0"?><rss/>'})
        self.assertEqual(r, "xml")

    def test_generic_header_still_probes_structure(self):
        r = dispatch({"headers": {"Content-Type": "text/plain"}, "body": '{"a": 1}'})
        self.assertEqual(r, "json")


class JsonXmlDistinctTest(unittest.TestCase):
    def test_json_parsed_to_object(self):
        self.assertEqual(parse({"body": '{"a": 1}'}), {"a": 1})

    def test_xml_parsed_to_element(self):
        root = parse({"body": "<a><b>1</b></a>"})
        self.assertEqual(root.tag, "a")
        self.assertEqual(root.find("b").text, "1")

    def test_invalid_json_failure_reason(self):
        r = parse({"headers": {"Content-Type": "application/json"}, "body": "{oops"})
        self.assertIsInstance(r, Failure)
        self.assertTrue(r.reason)


class ErrorPageTest(unittest.TestCase):
    def test_status_and_fingerprint(self):
        r = dispatch({
            "url": "http://a/x",
            "status": 500,
            "headers": {"Content-Type": "text/html"},
            "body": "<html><title>500 Internal Server Error</title></html>",
        })
        self.assertEqual(r, "error")
        self.assertGreaterEqual(r.confidence, 0.9)

    def test_traceback_fingerprint_without_status(self):
        r = dispatch({"body": "Traceback (most recent call last):\n ..."})
        self.assertEqual(r, "error")

    def test_error_page_parse_returns_text(self):
        body = "<html><title>404 Not Found</title></html>"
        self.assertEqual(parse({"status": 404, "body": body}), body)


class BinaryTest(unittest.TestCase):
    def test_magic_bytes(self):
        r = dispatch({"url": "http://a/img", "body": b"\x89PNG\r\n\x1a\n" + b"\x00" * 32})
        self.assertEqual(r, "binary")

    def test_null_bytes_heuristic(self):
        r = dispatch({"url": "http://a/blob", "body": b"\x01\x02\x00\x03" * 16})
        self.assertEqual(r, "binary")

    def test_parse_attachment_length_ok(self):
        body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
        r = parse({"url": "http://a/i.png", "body": body,
                   "headers": {"Content-Length": str(len(body))}})
        self.assertEqual(r["length"], len(body))
        self.assertEqual(r["data"], body)
        self.assertEqual(r["filename"], "i.png")

    def test_parse_attachment_length_mismatch(self):
        r = parse({"url": "http://a/i.png", "body": b"\x89PNG\r\n\x1a\n",
                   "headers": {"Content-Length": "999"}})
        self.assertIsInstance(r, Failure)
        self.assertIn("length", r.reason)
