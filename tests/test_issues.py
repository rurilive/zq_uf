import unittest

from asanparse import locate, parse, report, reset
from asanparse import issues as parser

SAMPLE = """ERROR: heap-use-after-free 0x55d0
#0 0x55d0 read_token parser.c:88
#1 0x55e0 parse_header parser.c:64
ALLOC 0x55d0 new_token token.c:20
ERROR: heap-buffer-overflow 0x7a10
#0 0x7a10 decode_bytes decode.c:31
ALLOC 0x7a10 copy_frame frame.c:12
"""

# 真实 ASan 输出：带 ==pid==、READ/WRITE、freed/allocated 段和 SUMMARY。
ASAN_UAF = """==12345==ERROR: AddressSanitizer: heap-use-after-free on address 0x602000000010 at pc 0x000000401234 bp 0x7ffd1234 sp 0x7ffd1230
READ of size 4 at 0x602000000010 thread T0
    #0 0x401234 in read_token /src/parser.c:88:12
    #1 0x401300 in parse_header /src/parser.c:64:3
freed by thread T0 here:
    #0 0x7f0000000001 in free (/build/libc.so+0x800)
    #1 0x401500 in drop_token /src/token.c:44:5
previously allocated by thread T0 here:
    #0 0x7f0000000002 in malloc (/build/libc.so+0x900)
    #1 0x401600 in new_token /src/token.c:20:9

SUMMARY: AddressSanitizer: heap-use-after-free /src/parser.c:88:12 in read_token
==12345==ABORTING
"""


def _uaf_with(address, pc):
    """把 ASAN_UAF 的出错地址和 pc 换掉，模拟同一问题在另一次运行里的报告。"""
    return ASAN_UAF.replace("0x602000000010", address).replace(
        "0x000000401234", pc
    ).replace("0x401234", pc)


class ParseTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_error_type_address_and_stack(self):
        issue = parse(SAMPLE)["issues"][0]
        self.assertEqual(issue["type"], "heap-use-after-free")
        self.assertEqual(issue["address"], "0x55d0")
        self.assertEqual(
            [frame["function"] for frame in issue["frames"]],
            ["read_token", "parse_header"],
        )

    def test_frames_keep_raw_line(self):
        issue = parse(SAMPLE)["issues"][1]
        self.assertEqual(
            issue["frames"][0]["raw"], "#0 0x7a10 decode_bytes decode.c:31"
        )

    def test_address_maps_to_source_file_and_line(self):
        issue = parse(SAMPLE)["issues"][0]
        self.assertEqual(issue["mapping"], "mapped")
        self.assertEqual(issue["source"]["file"], "parser.c")
        self.assertEqual(issue["source"]["line"], 88)
        self.assertEqual(issue["source"]["function"], "read_token")

    def test_alloc_location_is_kept(self):
        issue = parse(SAMPLE)["issues"][0]
        self.assertEqual(issue["alloc"]["file"], "token.c")
        self.assertEqual(issue["alloc"]["line"], 20)
        self.assertEqual(issue["alloc"]["address"], "0x55d0")

    def test_real_report_extracts_access_and_free_stack(self):
        issue = parse(ASAN_UAF)["issues"][0]
        self.assertEqual(issue["access"]["op"], "READ")
        self.assertEqual(issue["access"]["size"], 4)
        self.assertEqual(issue["free"]["function"], "drop_token")
        self.assertEqual(issue["free"]["file"], "/src/token.c")
        self.assertEqual(issue["alloc"]["function"], "new_token")
        self.assertEqual(issue["source"]["line"], 88)

    def test_unmapped_address_is_marked(self):
        text = (
            "ERROR: AddressSanitizer: heap-buffer-overflow on address 0x10 "
            "at pc 0x401234 bp 0x1 sp 0x2\n"
            "WRITE of size 8 at 0x10 thread T0\n"
            "    #0 0x401234 in do_thing\n"
            "    #1 0x401300 in main (libmain.so+0x3300)\n"
        )
        issue = parse(text)["issues"][0]
        self.assertEqual(issue["mapping"], "unmapped")
        self.assertFalse(issue["frames"][0]["mapped"])
        self.assertEqual(issue["frames"][1]["module"], "libmain.so")

    def test_faulting_address_prefers_access_line_over_frame_pc(self):
        # 头部没有 on address，帧里的地址是出错指令的 pc，不是被访问的地址。
        text = (
            "ERROR: AddressSanitizer: heap-buffer-overflow\n"
            "WRITE of size 4 at 0x602000000030 thread T0\n"
            "    #0 0x401234 in f /src/a.c:5:9\n"
        )
        issue = parse(text)["issues"][0]
        self.assertEqual(issue["address"], "0x602000000030")

    def test_symbols_resolve_frames_without_file_line(self):
        text = (
            "ERROR: AddressSanitizer: heap-buffer-overflow on address 0x10 "
            "at pc 0x401234 bp 0x1 sp 0x2\n"
            "WRITE of size 8 at 0x10 thread T0\n"
            "    #0 0x000000401234 in do_thing\n"
        )
        symbols = {"0x401234": ("src/thing.c", 42, "do_thing")}
        issue = parse(text, symbols=symbols)["issues"][0]
        self.assertEqual(issue["mapping"], "mapped")
        self.assertEqual(issue["source"]["file"], "src/thing.c")
        self.assertEqual(issue["source"]["line"], 42)


class GroupingTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_same_stack_different_address_merges_into_one_issue(self):
        parse(_uaf_with("0x602000000010", "0x000000401234"))
        result = parse(_uaf_with("0x603000000020", "0x000000409999"))
        self.assertEqual(result["count"], 1)
        issue = result["issues"][0]
        self.assertEqual(len(issue["occurrences"]), 2)
        self.assertEqual(issue["reports"], [1, 2])

    def test_different_stack_stays_separate(self):
        parse(SAMPLE)
        self.assertEqual(len(parser._ISSUES), 2)
        self.assertEqual(locate("issue-1")["type"], "heap-use-after-free")
        self.assertEqual(locate("issue-2")["type"], "heap-buffer-overflow")

    def test_address_diff_lists_each_address_and_source(self):
        parse(_uaf_with("0x602000000010", "0x000000401234"))
        result = parse(_uaf_with("0x603000000020", "0x000000409999"))
        issue = result["issues"][0]
        self.assertTrue(issue["address_changed"])
        self.assertEqual(
            issue["addresses"], ["0x602000000010", "0x603000000020"]
        )
        diff = issue["address_diff"]
        self.assertEqual(len(diff), 2)
        for entry in diff:
            self.assertTrue(entry["mapped"])
            self.assertEqual(entry["file"], "/src/parser.c")
            self.assertEqual(entry["line"], 88)
            self.assertEqual(entry["function"], "read_token")
        self.assertEqual([entry["reports"] for entry in diff], [[1], [2]])

    def test_single_address_has_no_diff(self):
        issue = parse(SAMPLE)["issues"][0]
        self.assertFalse(issue["address_changed"])
        self.assertEqual(issue["address_diff"], [])

    def test_normalized_addresses_are_one(self):
        parse(_uaf_with("0x0000006020000010", "0x000000401234"))
        result = parse(_uaf_with("0x6020000010", "0x000000401234"))
        issue = result["issues"][0]
        self.assertFalse(issue["address_changed"])

    def test_locate_returns_grouped_issue(self):
        parse(SAMPLE)
        issue = locate("issue-1")
        self.assertEqual(issue["type"], "heap-use-after-free")
        self.assertEqual(issue["occurrences"][0]["report"], 1)
        self.assertEqual(locate("issue-404"), {})


class FilterTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_filter_by_exact_type(self):
        result = parse(SAMPLE, error_type="heap-buffer-overflow")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["issues"][0]["type"], "heap-buffer-overflow")

    def test_filter_by_wildcard(self):
        result = parse(SAMPLE, error_type="heap-*")
        self.assertEqual(result["count"], 2)

    def test_types_reflect_returned_issues(self):
        result = parse(SAMPLE, error_type="heap-buffer-overflow")
        self.assertEqual(result["types"], {"heap-buffer-overflow": 1})

    def test_report_can_filter_by_type_list(self):
        parse(SAMPLE)
        body = report(error_type=["heap-use-after-free"])
        self.assertEqual(body["issues"], 1)
        self.assertEqual(body["types"], {"heap-use-after-free": 1})

    def test_bad_filter_is_rejected(self):
        with self.assertRaises(ValueError):
            parse(SAMPLE, error_type=5)

    def test_bad_filter_is_rejected_even_for_unparseable_report(self):
        with self.assertRaises(ValueError):
            parse("garbage", error_type=[5])
        with self.assertRaises(ValueError):
            report(error_type=[5])


class FailureTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_unrecognized_report_keeps_raw_text(self):
        text = "nothing useful here"
        result = parse(text)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["raw"], text)
        self.assertTrue(result["rejected"])
        self.assertEqual(locate("issue-1"), {})

    def test_partial_report_keeps_raw_and_parses_rest(self):
        text = (
            "ERROR: heap-use-after-free 0x11\n"
            "#0 0x11 f1 a.c:1\n"
            "#not a frame at all\n"
        )
        result = parse(text)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["raw"], text)
        self.assertEqual(len(result["rejected"]), 1)
        self.assertEqual(result["rejected"][0]["line"], 3)

    def test_missing_stack_and_alloc_is_rejected_not_guessed(self):
        result = parse("ERROR: heap-use-after-free 0x11\n")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["rejected"]), 1)

    def test_empty_report_is_empty_not_failed(self):
        self.assertEqual(parse("")["status"], "empty")

    def test_non_string_report_is_rejected(self):
        with self.assertRaises(ValueError):
            parse(123)


class LeakTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_each_leak_is_a_separate_issue(self):
        text = (
            "==7==ERROR: LeakSanitizer: detected memory leaks\n"
            "\n"
            "Direct leak of 32 byte(s) in 1 object(s) allocated from:\n"
            "    #0 0x7f00 in malloc (/lib/libc.so+0x800)\n"
            "    #1 0x401000 in make_buf /src/buf.c:15:9\n"
            "\n"
            "Direct leak of 8 byte(s) in 1 object(s) allocated from:\n"
            "    #0 0x7f00 in malloc (/lib/libc.so+0x800)\n"
            "    #1 0x401100 in make_name /src/name.c:22:3\n"
            "\n"
            "SUMMARY: AddressSanitizer: 40 byte(s) leaked in 2 allocation(s).\n"
        )
        result = parse(text)
        self.assertEqual(result["count"], 2)
        first = result["issues"][0]
        self.assertEqual(first["type"], "memory-leak")
        self.assertEqual(first["leak"]["bytes"], 32)
        self.assertEqual(first["source"]["file"], "/src/buf.c")
        self.assertEqual(first["source"]["line"], 15)

    def test_same_leak_site_merges_across_runs(self):
        text = (
            "==7==ERROR: LeakSanitizer: detected memory leaks\n"
            "Direct leak of 32 byte(s) in 1 object(s) allocated from:\n"
            "    #0 0x7f00 in malloc (/lib/libc.so+0x800)\n"
            "    #1 0x401000 in make_buf /src/buf.c:15:9\n"
        )
        parse(text)
        result = parse(text.replace("0x401000", "0x402000"))
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["issues"][0]["leak"]["total_bytes"], 64)


class StatusTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_report_counts_and_mapping_split(self):
        parse(SAMPLE)
        body = report()
        self.assertEqual(body["issues"], 2)
        self.assertEqual(body["occurrences"], 2)
        self.assertEqual(body["types"]["heap-use-after-free"], 1)
        self.assertEqual(body["mapped"], 2)
        self.assertEqual(body["unmapped"], 0)

    def test_report_tracks_address_changed_groups(self):
        parse(_uaf_with("0x602000000010", "0x000000401234"))
        parse(_uaf_with("0x603000000020", "0x000000409999"))
        body = report()
        self.assertEqual(body["issues"], 1)
        self.assertEqual(body["occurrences"], 2)
        self.assertEqual(body["address_changed"], 1)


if __name__ == "__main__":
    unittest.main()
