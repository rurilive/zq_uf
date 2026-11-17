"""演示：三种不同格式的同批报告 -> 归类、合并、对账、合批查看。"""

from sandboxreport import (
    UnknownReportFormatError,
    batch,
    inconsistent,
    parse,
    reset,
    summary,
)

flat_report = {
    "id": "s-001",
    "behaviors": [
        {"type": "network", "action": "connect 10.0.0.5:443", "seconds": 12},
        {"type": "file", "action": "write /tmp/a.tmp", "seconds": 3},
        {"type": "network", "action": "connect 10.0.0.5:443", "seconds": 9},
    ],
    "static": {"packer": "upx", "entropy": 7.4, "network": True},
}

# 同批样本，另一种常见格式：行为按节组织，字段名也不一样
sectioned_report = {
    "id": "s-002",
    "sections": [
        {"title": "Network Activity / 网络", "events": [
            {"detail": "connect 10.0.0.5:443", "at": 15},
        ]},
        {"title": "Process / 进程", "events": [
            {"op": "spawn powershell -enc ZQBj", "time": 4},
        ]},
        {"title": "File System / 文件", "events": [
            {"op": "write C:\\Users\\u\\drop.dll", "time": 6},
        ]},
    ],
    "static": {"network": True, "file_write": False},
}

# 第三种格式：纯文本逐行日志
text_report = """# id: s-003
[7] registry: set HKLM\\...\\CurrentVersion\\Run\\updater
[2] file: write C:\\Users\\u\\startup\\u.bat
static: packer=upx
"""

reset()

for report in (flat_report, sectioned_report, text_report):
    result = parse(report)
    print("=" * 60)
    print("样本 {id}（格式 {format}）：{entries} 条行为 / {events} 个事件".format(
        id=result["id"], format=result["format"],
        entries=result["total"]["entries"], events=result["total"]["events"]))
    for behavior in result["behaviors"]:
        flag = "关键" if behavior["is_key"] else "    "
        anomaly = behavior["anomaly"] or ""
        print("  [{flag}] 秒{seconds:<8} {type:<8} x{count:<2} {action} {anomaly}"
              .format(flag=flag, seconds=str(behavior["seconds"]),
                      type=behavior["type"], count=behavior["count"],
                      action=behavior["action"], anomaly=anomaly))
    if result["mismatches"]:
        for mismatch in result["mismatches"]:
            print("  ! 对账不一致: {feature} 声明={declared} 实际={observed}"
                  .format(**mismatch))

print("=" * 60)
print("按类型筛选：只看 s-001 的网络行为")
for behavior in summary("s-001", "network")["behaviors"]:
    print("  {action} 出现 {count} 次，秒数 {seconds}".format(**behavior))

print("=" * 60)
print("同批合并查看：")
merged = batch()
for behavior in merged["behaviors"]:
    print("  {type:<8} {action} 共 {count} 次，涉及样本 {samples}".format(
        type=behavior["type"], action=behavior["action"],
        count=behavior["count"], samples=",".join(behavior["samples"])))
print("  异常条目 {} 条；对账不一致样本: {}".format(
    len(merged["anomalies"]), merged["inconsistent_samples"] or "无"))

print("=" * 60)
print("静态特征对不上的样本：", [row["id"] for row in inconsistent()])

print("=" * 60)
print("不认识的报告格式：原文保留、明确报错")
unknown = {"id": "s-404", "payload": "unstructured blob"}
try:
    parse(unknown)
except UnknownReportFormatError as exc:
    print("  报错: {}".format(exc))
    print("  原文已保留: {}".format(exc.raw))
