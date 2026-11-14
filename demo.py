from depcycle import CycleChecker, check, suggest_breaks

graph = {"a": {"b": 1}, "b": {"c": 2}, "c": {"a": 3}}
print("cycles", check(graph))

suggestions = suggest_breaks(graph)
for s in suggestions:
    print("break", s["edge"], "weight", s["weight"], "affected", s["affected_count"])

# 热更新：改 c 的依赖前先做增量检查
checker = CycleChecker(graph)
print("preview c->b", checker.preview("c", ["b"]))
