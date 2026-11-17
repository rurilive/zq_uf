"""复测两次演示：修复没生效 → 修复生效，再按项目汇总。"""

from datetime import datetime, timedelta, timezone

from vulnretest import history, reset, retest

reset()
T0 = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)

# 修复前抓到的证据，作为后续每轮对照的基线
BEFORE = {
    "at": T0 - timedelta(days=1),
    "steps": [
        {"name": "提交恶意参数", "evidence": "https://evidence/V-7/before-submit.png"},
        {"name": "查看响应回显", "evidence": "https://evidence/V-7/before-echo.png"},
    ],
}
vuln = {"id": "V-7", "project": "shop", "ticket": "SEC-7"}

print("== 第一段：修复没生效 ==")
first = retest({
    **vuln,
    "baseline": BEFORE,
    "steps": [
        {"name": "提交恶意参数", "ok": True, "evidence": "https://evidence/V-7/r1-submit.png"},
        {
            "name": "查看响应回显",
            "ok": False,
            "reason": "响应仍原样回显 script 标签",
            "evidence": "https://evidence/V-7/r1-echo.png",
        },
    ],
}, now=T0)
print("结果:", first["result"], "工单:", first["ticket"], first["ticket_status"])
print("失败点:", first["failures"])
print("基线:", first["baseline"]["id"], first["evidence_before"])

print("\n== 第二段：修复生效，关闭；随后复现按新问题处理 ==")
second = retest({
    **vuln,
    "steps": [
        {"name": "提交恶意参数", "ok": True, "evidence": "https://evidence/V-7/r2-submit.png"},
        {"name": "查看响应回显", "ok": True, "evidence": "https://evidence/V-7/r2-echo.png"},
    ],
}, now=T0 + timedelta(hours=1))
print("结果:", second["result"], "工单:", second["ticket"], second["ticket_status"])
print("与上次差异:", second["diff"])
print("基线未变:", second["baseline"]["id"] == first["baseline"]["id"])

reappeared = retest({
    **vuln,
    "steps": [
        {"name": "提交恶意参数", "ok": True},
        {"name": "查看响应回显", "ok": False, "reason": "回显再次出现", "evidence": "https://evidence/V-7/r3-echo.png"},
    ],
}, now=T0 + timedelta(hours=2))
print("复现:", reappeared["result"], "新问题:", reappeared["new_issue"],
      "新工单:", reappeared["ticket"], "关联原工单:", reappeared["linked_ticket"])

print("\n== 按项目汇总 ==")
summary = history(project="shop")
for row in summary["records"]:
    print(row["at"], row["ticket"], row["result"], row["closed"])
print(summary["projects"])
