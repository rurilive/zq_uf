from asanparse import locate, parse, report, reset

# 简化的报告格式，第二段演示沿用。
SAMPLE = """ERROR: heap-use-after-free 0x55d0
#0 0x55d0 read_token parser.c:88
#1 0x55e0 parse_header parser.c:64
ALLOC 0x55d0 new_token token.c:20
ERROR: heap-buffer-overflow 0x7a10
#0 0x7a10 decode_bytes decode.c:31
ALLOC 0x7a10 copy_frame frame.c:12
"""


def section_one():
    """原有演示：按报告结构提取，地址映射到源码。"""
    reset()
    result = parse(SAMPLE)
    print("count", result["count"])
    issue = result["issues"][0]
    print("type", issue["type"], "mapping", issue["mapping"])
    print("source", issue["source"])
    print("alloc", issue["alloc"])
    print(locate("issue-2")["source"])
    print(report()["types"])


def section_two():
    """完整流程：同一问题多次运行按栈归并，地址差异单列，可按类型筛选。"""
    reset()

    run_one = """==101==ERROR: AddressSanitizer: heap-use-after-free on address 0x602000000010 at pc 0x000000401234 bp 0x7ffd sp 0x7ffd
READ of size 4 at 0x602000000010 thread T0
    #0 0x401234 in read_token /src/parser.c:88:12
    #1 0x401300 in parse_header /src/parser.c:64:3
freed by thread T0 here:
    #0 0x7f00 in free (/build/libc.so+0x800)
    #1 0x401500 in drop_token /src/token.c:44:5
previously allocated by thread T0 here:
    #0 0x7f01 in malloc (/build/libc.so+0x900)
    #1 0x401600 in new_token /src/token.c:20:9
SUMMARY: AddressSanitizer: heap-use-after-free /src/parser.c:88:12 in read_token
"""
    # 另一次运行：地址和行号都变了，但调用栈是同一处，应该归并到同一条目。
    run_two = """==102==ERROR: AddressSanitizer: heap-use-after-free on address 0x6030000000a0 at pc 0x000000409999 bp 0x7ffe sp 0x7ffe
READ of size 4 at 0x6030000000a0 thread T0
    #0 0x409999 in read_token /src/parser.c:91:16
    #1 0x409aaa in parse_header /src/parser.c:67:3
SUMMARY: AddressSanitizer: heap-use-after-free /src/parser.c:91:16 in read_token
"""

    first = parse(run_one)
    second = parse(run_two)
    print("run1", first["count"], "run2", second["count"])
    issue = locate("issue-1")
    print("merged occurrences", issue["count"], "reports", issue["reports"])
    print("address changed", issue["address_changed"], issue["addresses"])

    # 每次运行的地址差异单独列，映射到源码的给出文件行号和函数名。
    for entry in issue["address_diff"]:
        print("  ", entry["address"], "report", entry["reports"],
              "->", entry["file"], entry["line"], entry["function"],
              "mapped" if entry["mapped"] else "unmapped")

    # 归并后的条目可按问题类型筛选，方便批量修一类问题。
    print("heap-*", report(error_type="heap-*")["issues"])
    print("mapped/unmapped", report()["mapped"], report()["unmapped"])

    # 格式对不上时保留原文，不静默返回空结果。
    broken = parse("==103==ERROR: AddressSanitizer: heap-buffer-overflow\n"
                   "this line does not look like a stack frame\n")
    print("broken status", broken["status"], "raw kept", bool(broken["raw"]),
          "rejected", len(broken["rejected"]))


section_one()
section_two()
