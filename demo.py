import importlib

from tablearchive import archive, last_route, query, restore

module = importlib.import_module("tablearchive.archive")

# 跨年数据：2021-2024 每年两条
module._rows.extend(
    {"city": city, "year": year, "v": i}
    for i, (year, city) in enumerate(
        (year, city)
        for year in (2021, 2022, 2023, 2024)
        for city in ("sh", "bj")
    )
)
print("归档前在线:", len(module._rows), "行")

# 1) 按时间归档到冷层：2023 年之前的数据下冷
result = archive("t", {"before": 2023})
print("归档量:", result["archived"], "分区:", result["partition"], "范围:", result["range"])
print("归档后在线:", len(module._rows), "行")

# 2) 透明路由：查询接口不变，自动路由冷热并合并
rows = query("t", {"city": "sh"})
print("查询 city=sh:", [(r["year"], r["v"]) for r in rows])
print("路由:", last_route())

# 时间等值条件裁剪冷分区
query("t", {"year": 2024})
print("查询 year=2024 路由:", last_route())

# 3) 恢复：按元数据回迁并校验
back = restore("t")
print("恢复:", back)
print("恢复后在线:", len(module._rows), "行, 全量查询:", len(query("t", {})), "行")
