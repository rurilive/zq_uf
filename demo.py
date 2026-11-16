from assetmerge import MergeAbort, merge, preview, report, scan_list, split

ASSETS = [
    {"id": "a1", "host": "web1", "kind": "domain", "value": "www.example.com",
     "vulns": ["vuln-sqli"], "source": "crm",
     "resolves_to": ["10.0.0.8"]},
    {"id": "a2", "host": "web1", "kind": "ip", "value": "10.0.0.8",
     "vulns": ["vuln-sqli"], "source": "netscan"},
    {"id": "a3", "host": "web1", "kind": "port", "value": "10.0.0.8:443",
     "vulns": ["vuln-xss"], "source": "netscan"},
    {"id": "a4", "host": "web2", "kind": "ip", "value": "10.0.0.9",
     "vulns": ["vuln-xss"], "source": "netscan"},
]

# 第一段：按主机合并
out = merge(ASSETS)
print("== 合并 ==")
print("原始记录数", out["count"])
print("主机数量", out["group_count"])
print("别名数", out["alias_count"])
print("合并依据", out["basis"])
for group in out["groups"]:
    print(" 主机", group["host"],
          "主记录", group["primary_record"],
          "并入", group["merged_count"], "条",
          "依据", group["basis"],
          "别名", [a["value"] for a in group["aliases"]])
print("下发清单", scan_list())

rep = report()
print("== 报告（按主机）==")
print("主机数", rep["hosts"], "资产数", rep["records"], "别名数", rep["alias_count"])
for name, info in rep["vulns"].items():
    print(" 漏洞", name, "影响主机", info["hosts"], "受影响入口", info["entries"])

# 第二段：预览 -> 执行校验 -> 拆回原样
print("== 预览 ==")
pv = preview(ASSETS)
print("受影响记录条数", pv["affected_count"], "合并分组数", pv["merged_group_count"])

print("== 预览不一致时中止 ==")
tampered = dict(pv, affected_count=99)
try:
    merge(ASSETS, expect=tampered)
except MergeAbort as exc:
    print("已中止，差异：", exc.differences)
print("中止后资产数仍为", report()["records"])

print("== 拆分指定合并 ==")
web1_group = next(g["group_id"] for g in out["groups"] if g["host"] == "web1")
undone = split(group_id=web1_group)
print("拆分记录", undone["splits"])
print("拆分后主机数", report()["hosts"], "资产数", report()["records"])
print("下发清单恢复为", scan_list())
