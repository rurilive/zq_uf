from dagprop import hot_update, rerun, run

PIPELINE = {
    "nodes": [
        {"name": "extract"},
        {"name": "transform", "deps": ["extract"]},
        {"name": "load", "deps": ["transform"]},
    ]
}

attempts = {"extract": 0}


def execute(name):
    """模拟：extract 首次失败，其余节点正常。"""
    if name == "extract":
        attempts["extract"] += 1
        if attempts["extract"] == 1:
            raise RuntimeError("source unreachable")
    print(f"  exec {name}")
    return {"node": name}


print("1) 第一次调度：上游失败，下游跳过")
result = run(PIPELINE, execute=execute)
print(result["status"])
print("skipped:", result["skipped"], result["skip_reasons"])
print("rerun:", result["rerun"])

print("\n2) 只重跑失败和跳过的节点，成功的复用")
result = rerun(execute=execute)
print("rerun:", result["rerun"], "reused:", result["reused"])

print("\n3) 运行中新增依赖：校验后热更新，下一次调度生效")
update = hot_update({"nodes": [{"name": "report", "deps": ["load"]}]})
print("hot_update:", update["ok"], update["added"], "version", update["version"])
result = run(execute=execute)
print("rerun:", result["rerun"], "reused:", result["reused"])

print("\n4) 非高峰时段预演非法变更：校验失败，旧图继续调度")
update = hot_update({"deps": {"report": ["ghost"]}}, apply=False)
print("hot_update:", update["ok"], update["error"])
result = run(execute=execute)
print("old graph ok:", result["ok"], result["order"])
