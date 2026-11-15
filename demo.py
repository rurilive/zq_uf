"""混合结果批次演示：统计、对账、差异、下钻、导出。"""

import json

from crawlreport import build, drilldown, export, reconcile

batch = {
    "id": "b1",
    "rows": [
        {"id": "a1", "site": "a", "stage": "fetch", "status": "ok",
         "time": "2026-11-15T08:00:00"},
        {"id": "a2", "site": "a", "stage": "parse", "status": "failed",
         "reason": "http_500", "time": "2026-11-15T08:01:00"},
        {"id": "a3", "site": "a", "stage": "fetch", "status": "skipped",
         "reason": "duplicate", "time": "2026-11-15T08:02:00"},
        {"id": "a4", "site": "a", "stage": "export", "status": "ok",
         "time": "2026-11-15T08:03:00"},
        {"id": "b1", "site": "b", "stage": "fetch", "status": "ok",
         "time": "2026-11-15T08:04:00"},
        {"id": "b2", "site": "b", "stage": "export", "status": "failed",
         "reason": "db_timeout", "time": "2026-11-15T08:05:00"},
        {"site": "b", "stage": "fetch", "status": "ok"},  # 无 id，无法对账
        "raw-row",  # 非字典行，只计总数
    ],
    "stored": [
        {"id": "a1", "site": "a", "stage": "fetch", "status": "ok",
         "time": "2026-11-15T08:00:00"},
        {"id": "b1", "site": "b", "stage": "fetch", "status": "ok",
         "time": "2026-11-15T08:04:00"},
        {"id": "c9", "site": "c", "stage": "fetch", "status": "ok",
         "time": "2026-11-15T08:06:00"},  # 入库多余
        # a4 应入库但缺失
    ],
}

report = build(batch)

print("== 统计 ==")
print(json.dumps({k: report[k] for k in
                  ("total", "unparsed", "by_status", "by_site", "by_stage",
                   "by_reason")}, ensure_ascii=False, indent=2))

result = reconcile(report)
print("== 对账 ==")
print(json.dumps({k: result[k] for k in
                  ("matched", "row_count", "by_reason")},
                 ensure_ascii=False, indent=2))
print("校验和一致:", result["checksum"]["matched"])

print("== 差异（按站点、阶段排序） ==")
print(json.dumps(result["diff"], ensure_ascii=False, indent=2))

print("== 无法对账的行（单列） ==")
print(json.dumps(result["unreconcilable"], ensure_ascii=False, indent=2))

print("== 下钻：站点 a 的失败 ==")
print(json.dumps(drilldown(report, site="a", status="failed"),
                 ensure_ascii=False, indent=2))

export(report, "report-b1.json")
print("已导出 report-b1.json（含版本号 %s，生成时间 %s）"
      % (report["version"], report["generated_at"]))
