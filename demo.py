from metriclate2 import add, recompute, reset, versions

print(add({"window": "2026-11-15T10", "value": 1.5}))
print(add({"window": "2026-11-15T10", "value": 2.5}))
print(recompute("2026-11-15T10"))

# 第二段：迟到事件回到历史窗口，先单窗验证再扩大范围
reset()
add({"event_time": "2026-11-15T09:10:00", "value": 1.0})
add({"event_time": "2026-11-15T10:01:00", "value": 2.0})
late = add({"event_time": "2026-11-15T09:30:00", "value": 3.0})
print("迟到事件:", late)

# 人工核对：09 窗口应有 2 个事件，sum = 4.0
expected = {"count": 2, "sum": 4.0}
got = recompute("2026-11-15T09")
assert {"count": got["count"], "sum": got["sum"]} == expected, "与人工核对不一致，停止扩大范围"
print("历史窗口验证通过:", got)

# 验证通过后再扩大到其余窗口
print(recompute("2026-11-15T10"))

# 重复重算结果一致，版本历史可查询
again = recompute("2026-11-15T09")
assert (again["count"], again["sum"]) == (got["count"], got["sum"])
print("版本历史:", versions("2026-11-15T09"))
