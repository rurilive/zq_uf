from datasetcache import (
    clear,
    get,
    invalidate,
    preheat,
    put,
    register_dataset,
    report,
    set_version,
)

clear()
put("orders:1", {"rows": 3}, now=100)
print(get("orders:1", now=101))
print(invalidate("orders"))
print(get("orders:1", now=101))

# 第二段：维表更新，按版本和血缘失效
clear()
register_dataset("dim_user", version=1)
register_dataset("fact_orders", version=1, depends_on=["dim_user"])
register_dataset("fact_logistics", version=1)  # 不依赖 dim_user

preheat(["dim_user:1", "dim_user:2"], lambda k: {"rows": 10}, dataset="dim_user", now=100)
put("fact_orders:1", {"rows": 3}, dataset="fact_orders", now=100)
put("fact_logistics:1", {"rows": 5}, dataset="fact_logistics", now=100)

# 维表从版本 1 升到 2：只清 dim_user 和下游 fact_orders，分批限速
print(invalidate("dim_user", version=2, batch_size=1, rate=100))  # 3
print(get("fact_logistics:1", now=101))  # {'rows': 5}，未受影响
print(invalidate("dim_user", version=2))  # 0，版本未变不再清

# 旧值兜底：维表再次升版，回源失败时返回旧值
put("dim_user:9", {"rows": 1}, dataset="dim_user", now=100)
set_version("dim_user", 3)


def bad_loader(key):
    raise RuntimeError("origin down")


print(get("dim_user:9", now=101, loader=bad_loader, allow_stale=True))  # {'rows': 1}
print(report())
