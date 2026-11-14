import unittest

from contenttype import dispatch, parse


class DispatchTest(unittest.TestCase):
    def test_json_suffix(self):
        self.assertEqual(dispatch({"url": "http://a/x.json"}), "json")

    def test_html_default(self):
        self.assertEqual(dispatch({"url": "http://a/x"}), "html")

    def test_parse_text(self):
        self.assertEqual(parse({"body": "x"}), "x")
