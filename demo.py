"""演示：三种报告格式的解析、按类型筛选、批处理与合并查看。"""

from sandboxreport import (
    ReportParseError,
    merge_samples,
    parse,
    parse_batch,
    reset,
    static_mismatches,
    summary,
    unparsed,
)

reset()

# 格式一：dict 行为列表（v1）
report_v1 = {
    "id": "s-001",
    "behaviors": [
        {"type": "network", "action": "connect 10.0.0.5:443", "seconds": 12},
        {"type": "file", "action": "write /tmp/a.tmp", "seconds": 3},
        {"type": "network", "action": "connect 10.0.0.5:443", "seconds": 9},
    ],
    "static": {"packer": "upx", "entropy": 7.4},
}

# 格式二：dict 分节（v2），条目带显式可疑标记
report_v2 = {
    "id": "s-002",
    "sections": {
        "network": [
            {"action": "connect 1.2.3.4:4444", "time": "00:05", "suspicious": True},
            {"action": "dns query evil.xyz", "seconds": 20},
        ],
        "file": [{"action": "encrypt /data/1.doc", "seconds": 7}],
        "registry": [{"action": "set HKCU\\Run\\updater", "seconds": 11}],
    },
    "static": {"capabilities": ["socket", "CreateFileA", "RegSetValueExW"]},
}

# 格式三：文本报告，同一批样本里格式与前两份不同
report_text = """样本 ID: s-003

[网络行为]
[00:12] connect 10.0.0.5:443
[00:09] connect 10.0.0.5:443
[00:15] dns query bad.top [异常]

[进程行为]
[00:06] inject explorer.exe [可疑]

[静态特征]
packer=upx
capabilities=socket, CreateFileA
"""

print("== parse: 重复行为合并、按首次秒数排序 ==")
out = parse(report_v1)
for item in out["behaviors"]:
    flag = "关键" if item["key"] else ""
    print(f"  第{item['first_seconds']}秒 {item['type']}: "
          f"{item['action']} x{item['count']} {flag}")

print("\n== summary: 按样本汇总，可按行为类型筛选 ==")
print(summary("s-001")["by_type"])
print(summary("s-001", behavior_type="network")["behaviors"])

print("\n== 异常行为单独标出（规则 + 原文标记）==")
parse(report_v2)
for anomaly in summary("s-002")["anomalies"]:
    print(f"  第{anomaly['first_seconds']}秒 {anomaly['action']} "
          f"-> {', '.join(anomaly['reasons'])}")

print("\n== 文本格式 + 静态特征对账 ==")
parse(report_text)
for row in static_mismatches():
    print(f"  样本 {row['id']} 对不上:")
    for mismatch in row["mismatches"]:
        print(f"    - {mismatch['type']}: {mismatch['reason']}")

print("\n== 批处理：坏格式保留原文并明确报错 ==")
batch = parse_batch([report_v1, {"id": "s-bad", "sections": []}, report_text])
print(f"  成功 {len(batch['parsed'])} 份，失败 {len(batch['failed'])} 份")
for failure in batch["failed"]:
    print(f"  失败原因: {failure['error']}; 原文已留档: {failure['report']}")
print(f"  unparsed() 留档条数: {len(unparsed())}")

print("\n== 合并查看同一批样本，按 network 筛选 ==")
merged = merge_samples(behavior_type="network")
for item in merged["behaviors"]:
    samples = ", ".join(f"{s['id']}x{s['count']}" for s in item["samples"])
    print(f"  {item['action']} 共{item['count']}次 ({samples})")

print("\n== 完全不认识的格式 ==")
try:
    parse(12345)
except ReportParseError as exc:
    print(f"  {type(exc).__name__}: {exc.message}")
