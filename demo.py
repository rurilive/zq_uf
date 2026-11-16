from crashdedupe import report, triage

CRASHES = [
    {
        "id": "s1",
        "target": "pdf",
        "stack": ["parse_obj+0x12", "main+0x40"],
        "signal": "SIGSEGV",
    },
    {
        "id": "s2",
        "target": "pdf",
        "stack": ["parse_obj+0x31", "main+0x40"],
        "signal": "SIGABRT",
    },
    {
        "id": "s3",
        "target": "pdf",
        "stack": ["read_len+0x04", "main+0x40"],
        "signal": "SIGSEGV",
    },
]

result = triage(CRASHES)
print(result["issues"])
print(result["total"])
print(report())

# TODO: 第二段演示（实现后补）
