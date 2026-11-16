"""演示：会话级检测、规则版本、增量重跑与误报回灌。

运行：python3 -I demo.py（需在 repo 根目录，或把 repo 加入 sys.path）
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pcapflow import (
    analyze,
    compare,
    get_packet,
    load,
    mark_false_positive,
    query,
    report,
    reset,
    run_cases,
    FEEDBACK,
)

# 场景：10.0.0.5 的备份任务（连备份服务器 10.0.0.9 的 SMB，曾经被误报成扫描），
# 一次正常外联，以及对 4 台内网主机的 SSH 连接（横向扇出）。
PACKETS = [
    {"no": 1, "src": "10.0.0.5", "dst": "10.0.0.9", "sport": 49001, "dport": 445, "proto": "tcp", "ts": 1.0},
    {"no": 2, "src": "10.0.0.5", "dst": "10.0.0.9", "sport": 49001, "dport": 445, "proto": "tcp", "ts": 1.1},
    {"no": 3, "src": "10.0.0.5", "dst": "10.0.0.9", "sport": 49001, "dport": 445, "proto": "tcp", "ts": 1.2},
    {"no": 4, "src": "10.0.0.5", "dst": "203.0.113.7", "sport": 49002, "dport": 443, "proto": "tcp", "ts": 2.0},
    {"no": 5, "src": "10.0.0.5", "dst": "10.0.2.1", "sport": 49003, "dport": 22, "proto": "tcp", "ts": 3.0},
    {"no": 6, "src": "10.0.0.5", "dst": "10.0.2.2", "sport": 49004, "dport": 22, "proto": "tcp", "ts": 3.1},
    {"no": 7, "src": "10.0.0.5", "dst": "10.0.2.3", "sport": 49005, "dport": 22, "proto": "tcp", "ts": 3.2},
    {"no": 8, "src": "10.0.0.5", "dst": "10.0.2.4", "sport": 49006, "dport": 22, "proto": "tcp", "ts": 3.3},
    # 协议解析失败的畸形 TLS 包，会单独列出
    {"no": 9, "src": "10.0.0.5", "dst": "203.0.113.8", "sport": 49007, "dport": 443,
     "proto": "tcp", "ts": 4.0, "payload": b"\x01\x02garbage"},
]

reset()
print("== 载入：五元组归并 ==")
info = load("cap1", PACKETS)
print("包数量", info["packets"])

print("\n== 1.0.0 规则（横向扫描阈值 5，当前扇出 4 不告警）==")
out_v1 = analyze("cap1", rule_version="1.0.0")
print("会话数", out_v1["sessions"], "命中会话", out_v1["hit_sessions"],
      "规则版本", out_v1["rule_version"])
for hit in out_v1["hits"]:
    print(" ", hit["type"], hit["src"], "->", hit["dst"], "证据包", hit["evidence"])

print("\n== 升级到 1.1.0（阈值收紧到 3）：只重跑变更规则 ==")
out_v2 = analyze("cap1", rule_version="1.1.0")
print("重跑规则", out_v2["incremental"]["rerun_rules"])
print("复用规则", out_v2["incremental"]["reused_rules"])
for hit in out_v2["hits"]:
    print(" ", hit["type"], hit["src"], "->", hit["dst"], "|", hit["reason"])

print("\n== 备份任务不再误报：SMB 会话零命中 ==")
smb = next(s for s in out_v2["session_list"] if s["dport"] == 445)
print("SMB 会话包序号", smb["packets"], "判定", smb["types"] or "无")

print("\n== 版本对比 ==")
diff = compare("cap1", "1.0.0", "1.1.0")
print("新增", diff["summary"]["added"], "消失", diff["summary"]["removed"],
      "变化", diff["summary"]["changed"])

print("\n== 证据回放：取第一条横向扫描命中的证据包 ==")
scan = query(type="lateral_scan")[0]
print("证据包序号", scan["evidence"], "原始包", get_packet("cap1", scan["evidence"][0]))

print("\n== 误报标记并回灌成用例 ==")
# 安全同学确认 10.0.2.1 是运维巡检的批量登录，标记该会话为误报
patrol = query(host="10.0.2.1", type="lateral_scan")[0]
case = mark_false_positive("cap1", patrol["session_id"], "lateral_scan",
                           reason="运维巡检的批量登录")
print("用例", case["case_id"], "五元组", case["five_tuple"])
out_after = analyze("cap1", rule_version="1.1.0")
print("回灌后横向扫描命中数",
      sum(1 for h in out_after["hits"] if h["type"] == "lateral_scan"),
      "（标记前为 4，标记后该会话被抑制）")
case_results = run_cases(FEEDBACK, lambda p: analyze(p), ["cap1"])
print("用例回归状态", [(r["case_id"], r["status"]) for r in case_results])

print("\n== 报告：按主机查看 ==")
rep = report("cap1")
print("规则版本", rep["rule_version"], "类型分布", rep["types"])
print("主机", rep["hosts"]["10.0.0.5"]["types"])
print("解析失败包", rep["failed_packets"])

print("\n== 未覆盖协议单独列出 ==")
load("cap2", [
    {"no": 1, "src": "10.0.0.5", "dst": "203.0.113.9", "sport": 51000,
     "dport": 4789, "proto": "udp", "ts": 1.0},  # VXLAN，注册表不认识
])
out_unknown = analyze("cap2")
print("未覆盖", out_unknown["uncovered"], "失败", out_unknown["failed"])
print("未覆盖会话同时给异常端口结论：",
      [h["type"] for h in out_unknown["hits"]])

print("\n== 下钻：按主机+时间窗 ==")
print(query(host="10.0.0.5", start=3.0, end=3.2, type="lateral_scan"))
