"""影响分析演示：核心表字段改动 -> 多跳下游、层级、路径、环、断链、导出、回放。"""

from lineageimpact2 import Lineage, export, replay

# ---------------------------------------------------------------- 1. 建图
# 核心表选 ods.orders：下游有 dwd 明细、dws 聚合、ads 应用层。
core = "ods.orders"
g = Lineage()
g.add_edge("ods.orders.id", "dwd.order_fact.order_id")
g.add_edge("ods.orders.user_id", "dwd.order_fact.user_id")
g.add_edge("ods.orders.id", "dwd.order_log.order_id")
g.add_edge("dwd.order_fact.order_id", "dws.trade_agg.order_id")
g.add_edge("dwd.order_fact.user_id", "dws.trade_agg.user_id")
g.add_edge("dws.trade_agg.order_id", "ads.trade_board.order_id")
g.add_edge("dws.trade_agg.user_id", "ads.user_profile.user_id")
# 只知道表依赖、不知道字段映射的一段血缘 -> 断链
g.add_table_edge("ads.trade_board", "ads.board_cache")


def show(title):
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------- 2. 字段级多跳
show("1. 改 ods.orders.id：字段级多跳影响")
r = g.impact("ods.orders.id")
print(f"层级={r['depth']} 下游={r['total']} 个（直接 {r['direct_count']} / 间接 {r['indirect_count']}）")
for e in r["impacts"]:
    print(f"  L{e['level']} {e['status']:<8} {e['qualified']:<28} 影响={e['impact']:<6}")
    print(f"       路径 {' -> '.join(e['path'])}")
if r["breaks"]:
    for b in r["breaks"]:
        print(f"  断点：{b['from']} -> {b['to']}（{b['reason']}）")

# ---------------------------------------------------------------- 3. 核心表核对
show("2. 核心表核对：多跳路径与实际改动是否一致")
expected = [
    "dwd.order_fact.order_id",
    "dwd.order_log.order_id",
    "dws.trade_agg.order_id",
    "ads.trade_board.order_id",
    "ads.board_cache",  # 表级血缘只能定位到表，字段未知：算 partial 影响，且是断点终点
]
check = g.verify(core, "id", expected)
print(f"一致：{check['ok']}")
print(f"缺失：{check['missing'] or '无'}")
print(f"多出：{check['unexpected'] or '无'}")
print(f"独立改 user_id 只影响：{[e['qualified'] for e in g.impact(core, 'user_id')['impacts']]}")

# ---------------------------------------------------------------- 4. 环路
show("3. 血缘有环：截断并标出，不卡死")
g.add_edge("ads.trade_board.order_id", "ods.orders.id")  # 制造环
r = g.impact("ods.orders.id")
print(f"下游={r['total']} 截断={r['truncated']} 原因={r['truncation']}")
for c in r["cycles"]:
    print(f"  环：{' -> '.join(c['path'])}")
g.remove_edge("ads.trade_board.order_id", "ods.orders.id")

# ---------------------------------------------------------------- 5. 缓存
show("4. 缓存：同参命中，血缘变化后失效")
_ = g.impact("ods.orders.id")
print("第二次查询命中缓存：", g.impact("ods.orders.id")["cache"]["hit"])
g.add_edge("ads.user_profile.user_id", "app.portal.uid")
print("新增血缘后命中缓存：", g.impact("ods.orders.id")["cache"]["hit"],
      f"（版本 {g.version}）")

# ---------------------------------------------------------------- 6. 改名
show("5. 改名：不产生重复节点")
n_before = g.node_count()
g.rename_table("ads.user_profile", "ads.user_portrait")
g.rename_field(("dwd.order_fact", "order_id"), "order_key")
print(f"节点数 {n_before} -> {g.node_count()}（保持不变）")
print("表级下游：", g.impact(core)["downstream"])

# ---------------------------------------------------------------- 7. 导出 / 回放
show("6. 导出与回放")
r = g.impact("ods.orders.id")
print(export(r, "csv").splitlines()[0])
print("...共", len(export(r, "csv").splitlines()) - 1, "行")
rebuilt = replay(g.events())
print("回放后下游一致：",
      [e["qualified"] for e in rebuilt.impact("ods.orders.id")["impacts"]]
      == [e["qualified"] for e in r["impacts"]])
