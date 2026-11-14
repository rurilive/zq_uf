"""lineageimpact 演示：三跳链路 + 字段级 + 环路 + 缓存命中率。

运行：python3 demo.py
"""

from lineageimpact import cache_stats, impact, paths, record_field, reset


def show_impact(title, table, field=None):
    result = impact(table, field)
    print("\n== %s ==" % title)
    print("下游表（按影响程度排序）: %s" % list(result))
    for item in result.details:
        print("  表 %-6s 层级 %d  路径 %s" % (item["table"], item["level"], item["paths"]))
    if result.fields:
        for item in result.fields:
            print(
                "  字段 %s.%-8s 层级 %d  路径 %s"
                % (item["table"], item["field"], item["level"], item["paths"])
            )
    if result.cycles:
        print("  环路（已截断）: %s" % result.cycles)
    print("  缓存命中: %s" % result.cache_hit)


def main():
    reset()

    # 第一段：表级三跳链路 raw -> dwd -> dm -> ads。
    for src, dst in [("raw", "dwd"), ("dwd", "dm"), ("dm", "ads")]:
        record_field(src, "id", dst, "%s_id" % src)
    show_impact("表级三跳", "raw")

    # 第二段：字段级，只有被映射的字段才继续往下游传。
    record_field("raw", "phone", "dwd", "phone")
    record_field("dwd", "phone", "dm", "phone_hash")
    show_impact("字段级 raw.phone", "raw", "phone")
    show_impact("字段级 raw.id（会被截断在 dwd）", "raw", "id")

    # 第三段：paths(node) 给出到某个节点的命中路径。
    print("\n== 命中路径 ==")
    print("paths('ads')  ->", paths("ads"))
    print("paths('dm.phone_hash') ->", paths("dm.phone_hash"))

    # 第四段：环路检测与截断。
    record_field("ads", "x", "dwd", "x")
    show_impact("环路 dwd -> dm -> ads -> dwd", "dwd")
    reset()

    # 第五段：缓存。重复查询命中，血缘变更后失效。
    record_field("raw", "id", "dwd", "raw_id")
    record_field("dwd", "raw_id", "dm", "raw_id")
    impact("raw", "id")
    impact("raw", "id")
    impact("dwd", "raw_id")
    impact("dwd", "raw_id")
    record_field("dm", "raw_id", "ads", "raw_id")  # 变更 -> 缓存失效
    impact("raw", "id")
    paths("ads")
    paths("ads")

    stats = cache_stats()
    print("\n== 缓存（命中率单独输出）==")
    print("命中 %d 次 / 未命中 %d 次 / 总 %d" % (stats["hits"], stats["misses"], stats["total"]))
    for kind, counters in stats["by_kind"].items():
        print(
            "  %-6s 命中 %.0f%%（%d/%d）"
            % (
                kind,
                counters["hit_rate"] * 100,
                counters["hits"],
                counters["hits"] + counters["misses"],
            )
        )
    print("  总命中率 %.0f%%" % (stats["hit_rate"] * 100))


if __name__ == "__main__":
    main()
