"""asanparse 用法演示：
1) 单份报告解析 + locate 按条目查看；
2) 同一处问题在多次运行里的报告按栈归并，地址差异单独列出，按类型筛选。
"""

from asanparse import clear, groups, kept, locate, parse, report

clear()

SAMPLE = """ERROR: heap-use-after-free 0x55d0
#0 0x55d0 read_token parser.c:88
#1 0x55e0 parse_header parser.c:64
ALLOC 0x55d0 new_token token.c:20
ERROR: heap-buffer-overflow 0x7a10
#0 0x7a10 decode_bytes decode.c:31
ALLOC 0x7a10 copy_frame frame.c:12
"""

result = parse(SAMPLE, report_name="ci-run-1")
print("== 单份报告 ==")
print("count:", result["count"])
print("issue-1:", locate("issue-1"))
print("汇总:", report())

# 同一处问题，第二次运行栈完全相同，但地址变了（ASLR/堆布局不同）
RUN_B = """ERROR: heap-use-after-free 0x6110
#0 0x6110 read_token parser.c:88
#1 0x6120 parse_header parser.c:64
ALLOC 0x6110 new_token token.c:20
"""
parse(RUN_B, report_name="ci-run-2")

# 一份格式变了的报告：解析失败，原文保留，不静默吞掉
parse("==99==ERROR: NewSanitizer: brand-new-check\nno known structure\n", report_name="ci-run-3")

print("\n== 归并后按条目查看 ==")
for group in groups():
    print(
        group["id"], group["type"], "出现", group["count"], "次",
        "映射:", group["mapping"],
        "源码:", group["mapped_source"],
    )
    for variant in group["addresses"]:
        print("   地址差异:", variant)

print("\n== 只看 use-after-free（批量修复用） ==")
for group in groups("heap-use-after-free"):
    print(group["id"], group["function"], group["mapped_source"])

print("\n== 解析失败的原文保留 ==")
for entry in kept():
    print(entry["id"], entry["reason"], "->", repr(entry["raw"]))
