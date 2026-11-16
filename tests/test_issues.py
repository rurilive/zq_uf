import unittest

from asanparse import locate, parse, report
from asanparse import issues as parser

SAMPLE = """ERROR: heap-use-after-free 0x55d0
#0 0x55d0 read_token parser.c:88
#1 0x55e0 parse_header parser.c:64
ALLOC 0x55d0 new_token token.c:20
ERROR: heap-buffer-overflow 0x7a10
#0 0x7a10 decode_bytes decode.c:31
ALLOC 0x7a10 copy_frame frame.c:12
"""


class ParseTest(unittest.TestCase):
    def setUp(self):
        parser._ISSUES.clear()

    def test_counts_blocks(self):
        self.assertEqual(parse(SAMPLE)["count"], 2)

    def test_type_and_address(self):
        issue = parse(SAMPLE)["issues"][0]
        self.assertEqual(issue["type"], "heap-use-after-free")
        self.assertEqual(issue["address"], "0x55d0")

    def test_frames_keep_raw_line(self):
        issue = parse(SAMPLE)["issues"][1]
        self.assertEqual(issue["frames"][0]["raw"], "#0 0x7a10 decode_bytes decode.c:31")

    def test_source_is_not_mapped(self):
        self.assertEqual(parse(SAMPLE)["issues"][0]["source"], {})

    def test_unknown_format_returns_empty(self):
        self.assertEqual(parse("nothing useful here")["count"], 0)
        self.assertEqual(locate("issue-1"), {})
        self.assertEqual(report()["mapped"], 0)
