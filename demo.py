from vulnretest import history, retest

vuln = {"id": "V-7", "project": "shop", "ticket": "SEC-1201"}

# 修复前的验证步骤和证据作为基线随漏洞传入。
baseline = {
    "at": "2026-11-01T09:00:00Z",
    "steps": [
        {"name": "提交恶意参数", "ok": False, "evidence": "http://evidence/old/1"},
        {"name": "查看响应回显", "ok": False, "evidence": "http://evidence/old/2"},
    ],
}
print(retest({**vuln, "baseline": baseline, "steps": [
    {"name": "提交恶意参数", "ok": True, "evidence": "http://evidence/new/1"},
    {"name": "查看响应回显", "ok": False, "evidence": "http://evidence/new/2",
     "reason": "响应仍回显脚本"},
]}))
print(retest({**vuln, "baseline": baseline, "steps": [
    {"name": "提交恶意参数", "ok": True, "evidence": "http://evidence/new/1"},
    {"name": "查看响应回显", "ok": True, "evidence": "http://evidence/new/2"},
]}))

first = retest({**vuln, "baseline": baseline, "steps": [
    {"name": "提交恶意参数", "ok": True, "evidence": "http://evidence/new/1"},
    {"name": "查看响应回显", "ok": True, "evidence": "http://evidence/new/2"},
]})
print(first["result"], first["ticket"])

# 关闭后再次复现，按新问题处理并关联原工单。
again = retest({**vuln, "steps": [
    {"name": "提交恶意参数", "ok": False, "evidence": "http://evidence/again/1"},
    {"name": "查看响应回显", "ok": False, "evidence": "http://evidence/again/2"},
]})
print(again["recurrence"])

print(history("V-7")["count"], history("V-7")["by_project"])
print(history(project="shop")["count"])
