from rowdedup3 import export, merge, replay, report, reset

reset()

# 1) 旧版本后到不能盖新版本
print("== 版本优先 ==")
print(merge([
    {"table": "orders", "key": "a", "version": 2, "ts": 20, "amount": 2, "source": "new"},
    {"table": "orders", "key": "a", "version": 1, "ts": 10, "amount": 1, "source": "old"},
])["rows"])

# 2) 同版本：空值不覆盖有效值，冲突保留两侧
reset()
print("\n== 冲突保留 ==")
result = merge([
    {"table": "orders", "key": "a", "version": 3, "ts": 30, "amount": 100, "note": "x", "source": "s1"},
    {"table": "orders", "key": "a", "version": 3, "ts": 30, "amount": 120, "note": None, "source": "s2"},
])
print(result["rows"])
print("冲突:", result["conflicts"])
print("丢弃:", result["dropped_details"])

# 3) 版本缺失按时间补齐，报表链路金额不重复
reset()
print("\n== 报表链路 ==")
merge([
    {"table": "orders", "key": "a", "version": 1, "ts": 10, "amount": 50, "source": "s1"},
    {"table": "orders", "key": "a", "ts": 20, "amount": 60, "source": "s2"},
])
merge([
    {"table": "orders", "key": "b", "version": 1, "ts": 15, "amount": 30, "source": "s1"},
])
total = sum(row["amount"] for row in replay()["rows"])
print("汇总金额:", total)
print("按表统计:", report()["stats"])

# 4) 回放与导出
print("\n== 回放 ==")
print("回放一致:", replay() == replay())
print("导出记录数:", len(export()["records"]))
