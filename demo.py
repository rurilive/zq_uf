from txrollback import STORE, commit, reset, rollback, stats

reset()

# 半途失败：k1 已写入，k2 失败
try:
    commit({"id": "b1", "rows": [{"key": "k1"}, {"key": "k2", "fail": True}]})
except RuntimeError as exc:
    print("失败", exc)
print("库中仍有", sorted(STORE))

# 按补偿日志回滚 b1
result = rollback("b1")
print("回滚补偿", result["compensated"], "待人工", len(result["pending"]))
print("库中剩余", sorted(STORE))

# 补偿失败进待人工队列：k9 提交后被外部修改
commit({"id": "b2", "rows": [{"key": "k9"}]})
STORE["k9"] = {"key": "k9", "note": "外部修改"}
result = rollback("b2")
print("补偿失败待人工", [item["key"] for item in result["pending"]])

summary = stats()
print("统计 提交", summary["committed"], "回滚", summary["rolled_back"], "待处理", summary["pending"])
