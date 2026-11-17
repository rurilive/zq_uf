import unittest

from asanparse import clear, groups, locate, parse, report
from asanparse import issues as parser

SAMPLE = """ERROR: heap-use-after-free 0x55d0
#0 0x55d0 read_token parser.c:88
#1 0x55e0 parse_header parser.c:64
ALLOC 0x55d0 new_token token.c:20
ERROR: heap-buffer-overflow 0x7a10
#0 0x7a10 decode_bytes decode.c:31
ALLOC 0x7a10 copy_frame frame.c:12
"""

# 同一处问题（同栈同类型），但两次运行报告的地址不同
RUN_A = """ERROR: heap-use-after-free 0x55d0
#0 0x55d0 read_token parser.c:88
#1 0x55e0 parse_header parser.c:64
ALLOC 0x55d0 new_token token.c:20
"""

RUN_B = """ERROR: heap-use-after-free 0x6110
#0 0x6110 read_token parser.c:88
#1 0x6120 parse_header parser.c:64
ALLOC 0x6110 new_token token.c:20
"""

REAL_ASAN = """=================================================================
==12345==ERROR: AddressSanitizer: heap-use-after-free on address 0x602000000050 at pc 0x0000004012bb bp 0x7ffd1 sp 0x7ffd0
READ of size 4 at 0x602000000050 thread T0
    #0 0x4012ba in read_token parser.c:88:9
    #1 0x401455 in parse_header parser.c:64:5
    #2 0x7f123 in __libc_start_main (/lib/x86_64-linux-gnu/libc.so.6+0x29d90)

freed by thread T0 here:
    #0 0x400f10 in free_token token.c:42:3
    #1 0x401200 in read_token parser.c:80:5

previously allocated by thread T0 here:
    #0 0x400aa0 in malloc
    #1 0x400b22 in new_token token.c:20:7

SUMMARY: AddressSanitizer: heap-use-after-free parser.c:88:9 in read_token
==12345==ABORTING
"""

UNMAPPED = """ERROR: SEGV on unknown address 0x000000000000
#0 0x4012ba
#1 0x401455 parse_header
"""

BROKEN = """==999==ERROR: AddressSanitizer: unknown-new-check
some brand new format with no frames at all
"""


class ParseTest(unittest.TestCase):
    def setUp(self):
        clear()

    def test_counts_blocks(self):
        self.assertEqual(parse(SAMPLE)["count"], 2)

    def test_type_and_address(self):
        issue = parse(SAMPLE)["issues"][0]
        self.assertEqual(issue["type"], "heap-use-after-free")
        self.assertEqual(issue["address"], "0x55d0")

    def test_frames_keep_raw_line(self):
        issue = parse(SAMPLE)["issues"][1]
        self.assertEqual(issue["frames"][0]["raw"], "#0 0x7a10 decode_bytes decode.c:31")

    def test_source_maps_to_file_line_function(self):
        issue = parse(SAMPLE)["issues"][0]
        self.assertEqual(issue["mapping"], "mapped")
        self.assertEqual(issue["source"], {"file": "parser.c", "line": 88, "column": None})
        self.assertEqual(issue["frames"][0]["function"], "read_token")
        self.assertEqual(issue["alloc"]["source"]["file"], "token.c")
        self.assertEqual(issue["alloc"]["source"]["line"], 20)
        self.assertEqual(issue["alloc"]["function"], "new_token")

    def test_unmapped_is_explicit_not_silent(self):
        result = parse(UNMAPPED)
        issue = result["issues"][0]
        self.assertEqual(issue["mapping"], "unmapped")
        self.assertEqual(issue["source"], {})
        self.assertEqual(issue["frames"][0]["mapping"], "unmapped")
        # 第二帧有函数名但没有源码位置，同样是 unmapped
        self.assertEqual(issue["frames"][1]["function"], "parse_header")
        self.assertIsNone(issue["frames"][1]["source"])

    def test_real_asan_report_structure(self):
        result = parse(REAL_ASAN)
        self.assertEqual(result["count"], 1)
        issue = result["issues"][0]
        self.assertEqual(issue["type"], "heap-use-after-free")
        self.assertEqual(issue["address"], "0x602000000050")
        self.assertEqual(issue["frames"][0]["source"]["line"], 88)
        self.assertEqual(issue["frames"][0]["source"]["column"], 9)
        self.assertEqual(issue["frames"][2]["mapping"], "unmapped")  # libc 帧无源码
        self.assertEqual(len(issue["freed_frames"]), 2)
        self.assertEqual(issue["freed_frames"][0]["source"]["line"], 42)
        self.assertEqual(len(issue["alloc_frames"]), 2)
        self.assertEqual(issue["alloc"]["function"], "malloc")
        self.assertIsNone(issue["alloc"]["source"])  # malloc 帧本身无源码
        self.assertEqual(issue["alloc_frames"][1]["source"]["file"], "token.c")
        self.assertEqual(issue["alloc_frames"][1]["function"], "new_token")
        self.assertEqual(issue["summary"]["function"], "read_token")
        self.assertEqual(issue["summary"]["source"]["line"], 88)

    def test_same_issue_merged_by_stack_across_reports(self):
        parse(RUN_A, "run-a")
        result = parse(RUN_B, "run-b")
        self.assertEqual(result["count"], 1)
        self.assertEqual(len(groups()), 1)
        group = groups()[0]
        self.assertEqual(group["type"], "heap-use-after-free")
        self.assertEqual(group["count"], 2)
        self.assertEqual(group["issue_ids"], ["issue-1", "issue-2"])
        self.assertEqual(group["mapping"], "mapped")
        self.assertEqual(group["mapped_source"]["file"], "parser.c")
        self.assertEqual(group["function"], "read_token")
        # 两次报告地址不同，单独列出且都给出文件行号和函数名
        addresses = group["addresses"]
        self.assertEqual({a["address"] for a in addresses}, {"0x55d0", "0x6110"})
        for variant in addresses:
            self.assertEqual(variant["file"], "parser.c")
            self.assertEqual(variant["line"], 88)
            self.assertEqual(variant["function"], "read_token")
            self.assertEqual(variant["mapping"], "mapped")

    def test_different_stacks_are_different_groups(self):
        result = parse(SAMPLE)
        self.assertEqual(len(result["groups"]), 2)

    def test_unmapped_stack_merges_across_reports(self):
        run1 = "ERROR: SEGV on unknown address 0x1000\n#0 0xaaaa crash_now\n#1 0xbbbb main\n"
        run2 = "ERROR: SEGV on unknown address 0x2000\n#0 0xcccc crash_now\n#1 0xdddd main\n"
        parse(run1, "r1")
        parse(run2, "r2")
        self.assertEqual(len(groups("SEGV")), 1)
        group = groups("SEGV")[0]
        self.assertEqual(group["count"], 2)
        self.assertEqual(group["mapping"], "unmapped")
        self.assertIsNone(group["mapped_source"])
        self.assertEqual(
            {a["address"] for a in group["addresses"]}, {"0x1000", "0x2000"}
        )
        for variant in group["addresses"]:
            self.assertEqual(variant["mapping"], "unmapped")
            self.assertIsNone(variant["file"])

    def test_locate_issue_and_group(self):
        parse(RUN_A, "run-a")
        parse(RUN_B, "run-b")
        issue = locate("issue-2")
        self.assertEqual(issue["type"], "heap-use-after-free")
        self.assertEqual(issue["address"], "0x6110")
        group_id = issue["group"]
        group = locate(group_id)
        self.assertEqual(group["kind"], "group")
        self.assertEqual(group["count"], 2)
        self.assertEqual(locate("issue-999"), {})

    def test_groups_filter_by_type(self):
        parse(SAMPLE)
        uaf = groups("heap-use-after-free")
        self.assertEqual(len(uaf), 1)
        self.assertEqual(uaf[0]["type"], "heap-use-after-free")
        self.assertEqual(len(groups("heap-buffer-overflow")), 1)
        self.assertEqual(len(groups("no-such-type")), 0)

    def test_report_summary_and_filter(self):
        parse(RUN_A)
        parse(RUN_B)
        parse(SAMPLE)
        summary = report()
        self.assertEqual(summary["issues"], 4)
        self.assertEqual(len(summary["groups"]), 2)
        self.assertEqual(summary["types"]["heap-use-after-free"], 3)
        self.assertGreaterEqual(summary["mapped"], 2)
        filtered = report("heap-buffer-overflow")
        self.assertEqual(filtered["issues"], 1)
        self.assertEqual(len(filtered["groups"]), 1)

    def test_unknown_text_kept_not_silent_empty(self):
        result = parse("nothing useful here")
        self.assertEqual(result["count"], 0)
        self.assertEqual(len(result["kept"]), 1)
        kept = result["kept"][0]
        self.assertIn("nothing useful here", kept["raw"])
        self.assertTrue(kept["reason"])
        # 保留的原文可以通过 locate 取回
        self.assertEqual(locate(kept["id"])["raw"], kept["raw"])

    def test_broken_asan_block_keeps_raw(self):
        result = parse(BROKEN)
        self.assertEqual(result["count"], 0)
        self.assertEqual(len(result["kept"]), 1)
        self.assertIn("unknown-new-check", result["kept"][0]["raw"])
        self.assertEqual(report()["groups"], [])

    def test_registry_accumulates_across_parse_calls(self):
        parse(RUN_A, "run-a")
        parse(RUN_B, "run-b")
        self.assertEqual(len(groups()), 1)
        clear()
        self.assertEqual(report()["issues"], 0)
        self.assertEqual(groups(), [])

    def test_non_string_raises(self):
        with self.assertRaises(ValueError):
            parse(b"bytes not allowed")

    def test_lsan_leak_sections(self):
        lsan = """==77==ERROR: LeakSanitizer: detected memory leaks
Direct leak of 16 byte(s) in 1 object(s) allocated from:
    #0 0x401000 in malloc
    #1 0x401234 in cache_init cache.c:15:3
Indirect leak of 8 byte(s) in 1 object(s) allocated from:
    #0 0x401000 in malloc
    #1 0x401345 in node_add node.c:22:1
SUMMARY: LeakSanitizer: 24 byte(s) leaked in 2 allocation(s).
"""
        result = parse(lsan)
        self.assertEqual(result["count"], 2)
        kinds = {i["leak_kind"] for i in result["issues"]}
        self.assertEqual(kinds, {"direct", "indirect"})
        self.assertEqual(result["issues"][0]["type"], "memory-leak")


if __name__ == "__main__":
    unittest.main()
