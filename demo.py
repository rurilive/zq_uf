# -*- coding: utf-8 -*-
"""formstep 演示：检索页四步采集的保存、回退、幂等与条件复用。"""
from formstep import (DEFAULT_STEPS, back, begin, copy_flow,
                      export_conditions, import_conditions, step)

STEPS = list(DEFAULT_STEPS)  # industry -> region -> time_range -> crawl


def brief(r):
    return {"step": r["step"], "token": r["token"], "replayed": r["replayed"],
            "rolled_back": r["rolled_back"], "ok": r["ok"],
            "error": r.get("error"), "field": r.get("field"),
            "state": r["state"], "gen": r["page"]["generation"]}


# 1. 批次 A：逐页提交，条件按批次保存
r = begin("batch-A", STEPS)
for condition in ({"industry": "制造"}, {"region": "华东"},
                  {"time_range": "2026"}, {"crawl": 3}):
    r = step("batch-A", condition, token=r["token"])
print("1. 四步完成:", brief(r))
print("   结果集只生成一次:", [row["id"] for row in r["result"]["rows"]])

# 2. 重复提交同一令牌 -> replayed，仍是同一套结果
#    末步成功消耗的是第三步令牌 batch-A-3
again = step("batch-A", {"crawl": 99}, token="batch-A-3")
print("2. 重复提交:", brief(again), "同一结果集:", again["result"] == r["result"])

# 3. 回退到地区(第2步)：时间/翻页作废，行业保留，页面重新获取（新令牌）
r = back("batch-A", "region")
print("3. 回退:", brief(r))

# 4. 提交失败（缺条件）：令牌不消耗、已选条件保留，修复后原步骤继续
bad = step("batch-A", {}, token=r["token"])
print("4. 失败:", brief(bad))
ok = step("batch-A", {"region": "华南"}, token=r["token"])
print("   修复后原步骤继续:", brief(ok))
r = step("batch-A", {"time_range": "2025"}, token=ok["token"])
r = step("batch-A", {"crawl": 2}, token=r["token"])
print("   批次重新结束:", r["finished"], "页数:", r["result"]["pages"])

# 5. 批次间条件隔离
b = begin("batch-B", STEPS)
step("batch-B", {"industry": "零售"}, token=b["token"])
print("5. 批次隔离: A 的条件 =", r["state"], "; B 的条件 =",
      step("batch-B", {"industry": "零售"}, token=b["token"])["state"])

# 6. 导出 A，导入为新批次：从原步骤（末步）继续，不覆盖 A
payload = export_conditions("batch-A")
imported = import_conditions(payload, "batch-C")
print("6. 导入 C 停在第", imported["step"], "步，保留条件:", imported["state"])
again = import_conditions(payload, "batch-C")
print("   重复导入被拒绝:", again["ok"] is False, again["error"])
resumed = step("batch-C", {"crawl": 5}, token=imported["token"])
print("   C 从末步继续抓取:", resumed["result"]["pages"], "页")

# 7. 直接复制到另一个新批次
copied = copy_flow("batch-A", "batch-D")
print("7. 复制 D: 第", copied["step"], "步, 条件 =", copied["conditions"])
