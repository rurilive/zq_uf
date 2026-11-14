"""演示两端并发修改：版本向量、来源优先级、墓碑和冲突回查。"""

from rowmerge import (
    audit,
    discards,
    lookup,
    merge,
    report,
    reset,
    set_source_priority,
)


def hr(title):
    print(f"\n=== {title} ===")


def show_row(row):
    return dict(row)


PRIORITY = {"region-a": 10, "region-b": 1}

# 两端从同一起点出发：主键 1，版本 {repo: 1}。
base = {"id": 1, "name": "王五", "stock": 10, "version": {"repo": 1}, "updated_at": 1000}

# region-a：改名并把库存改成 12。
from_a = {
    "id": 1,
    "name": "王五（A 端）",
    "stock": 12,
    "version": {"repo": 1, "region-a": 1},
    "updated_at": 1010,
    "source": "region-a",
}

# region-b：同一行把库存改成 8。与 A 互不可见，属于并发修改。
from_b = {
    "id": 1,
    "name": "王五",
    "stock": 8,
    "version": {"repo": 1, "region-b": 1},
    "updated_at": 1020,
    "source": "region-b",
}


def concurrent_round(incoming) -> dict:
    """从同一起点出发按给定顺序重放一遍，返回最终行与累计冲突/丢弃。

    merge() 每次只报本批次的冲突和丢弃，所以这里把各批次累积起来。
    """
    reset(include_priority=True)
    set_source_priority(PRIORITY)
    merge([base], batch="base")
    conflicts, discarded = [], []
    for row in incoming:
        result = merge([row], batch=f"{row['source']}->hub")
        conflicts.extend(result["conflicts"])
        discarded.extend(result["discards"])
    return {
        "rows": result["rows"],
        "conflicts": conflicts,
        "discards": discarded,
    }


hr("两端并发修改同一行")
res_a_then_b = concurrent_round([from_a, from_b])
print("先 A 后 B，收敛行：", show_row(res_a_then_b["rows"][1]))
print("冲突：", [(c["key"], c["reason"], c["resolution"]) for c in res_a_then_b["conflicts"]])
print("保留两侧：", [(v["source"], v["name"], v["stock"])
                    for v in res_a_then_b["conflicts"][0]["values"]])

res_b_then_a = concurrent_round([from_b, from_a])
print("\n先 B 后 A，收敛行：", show_row(res_b_then_a["rows"][1]))
print("两种到达顺序收敛一致：", res_a_then_b["rows"][1] == res_b_then_a["rows"][1])
print("说明：两版并发、来源不同，按来源优先级 region-a(10) > region-b(1) 选出胜者，"
      "两侧值都留在冲突记录里。")

hr("同一版本、来源优先级仲裁")
reset(include_priority=True)
set_source_priority(PRIORITY)
tie_base = {"id": 2, "name": "李四", "version": {"repo": 1}, "updated_at": 1000}
tie_a = {**tie_base, "name": "李四（A）", "version": {"repo": 1, "region-a": 1},
         "updated_at": 1010, "source": "region-a"}
tie_b = {**tie_base, "name": "李四（B）", "version": {"repo": 1, "region-a": 1},
         "updated_at": 1010, "source": "region-b"}
merge([tie_base], batch="base")
merge([tie_b], batch="B->hub")
tie_res = merge([tie_a], batch="A->hub")
print("同版本同时间，胜者：", tie_res["rows"][2]["name"], "来源：", tie_res["rows"][2]["source"])
print("冲突记录：", [(c["key"], c["reason"], c["resolution"]) for c in tie_res["conflicts"]])

hr("墓碑：旧更新不能复活")
reset(include_priority=True)
set_source_priority(PRIORITY)
merge([base], batch="base")
# 中心先把 A 的修改同步给 B，B 基于 A 的版本删除。
merge([from_a], batch="A->hub")
merge([from_a], batch="hub->B")
deleted_by_b = {
    "id": 1,
    "version": {"repo": 1, "region-a": 1, "region-b": 1},
    "updated_at": 1040,
    "source": "region-b",
    "deleted": True,
}
merge([deleted_by_b], batch="B 删除")
# A 离线时的旧修改重放：版本被删除版本严格支配，不能复活。
res = merge([from_a], batch="A 旧值重放")
print("行状态：", res["rows"][1])
print("丢弃：", [(d["key"], d["reason"], d["source"]) for d in res["discards"]])

# 并发删除和并发更新相遇：删除优先（remove-wins）。
concurrent_delete = {
    "id": 3,
    "version": {"repo": 1, "region-b": 1},
    "updated_at": 1030,
    "source": "region-b",
    "deleted": True,
}
concurrent_update = {
    "id": 3,
    "name": "赵六（A 端）",
    "version": {"repo": 1, "region-a": 1},
    "updated_at": 1035,
    "source": "region-a",
}
merge([{"id": 3, "name": "赵六", "version": {"repo": 1}, "updated_at": 1000}], batch="base")
merge([concurrent_update], batch="A->hub")
race = merge([concurrent_delete], batch="B->hub")
print("并发删除 vs 更新，行状态：", race["rows"][3])
print("冲突：", [(c["key"], c["reason"], c["resolution"]) for c in race["conflicts"]])

hr("按主键回查冲突和丢弃")
print("lookup(1) 的批次：", lookup(1)["batches"])
print("discards(key=1)：")
for record in discards(key=1):
    print("  ", record["batch"], record["reason"], record["source"])
print("\nlookup(3) 的批次：", lookup(3)["batches"])
print("report(key=3)：")
for conflict in report(key=3):
    print("  ", conflict["batch"], conflict["reason"], conflict["resolution"],
          [(v["source"], v.get("deleted", False)) for v in conflict["values"]])
print("discards(key=3)：")
for record in discards(key=3):
    print("  ", record["batch"], record["reason"], record["source"])

hr("按批次归档审计")
for entry in audit():
    print(
        f"批次 {entry['batch']!r}: 输入 {entry['input']} 行, "
        f"冲突 {len(entry['conflicts'])} 条, 丢弃 {len(entry['discards'])} 条"
    )
