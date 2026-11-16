from asanparse import locate, parse, report

SAMPLE = """ERROR: heap-use-after-free 0x55d0
#0 0x55d0 read_token parser.c:88
#1 0x55e0 parse_header parser.c:64
ALLOC 0x55d0 new_token token.c:20
ERROR: heap-buffer-overflow 0x7a10
#0 0x7a10 decode_bytes decode.c:31
ALLOC 0x7a10 copy_frame frame.c:12
"""

result = parse(SAMPLE)
print("count", result["count"])
print(result["issues"][0])
print(locate("issue-2"))
print(report())

# TODO: 第二段演示（实现后补）
