"""metricrollup 演示：跨窗落窗、迟到重算、分位数草图与维度上下钻。"""

from metricrollup import add, query, reset


def show(title, result):
    print(f"\n== {title} ==")
    print(f"dim={result['dim']} count={result['count']} sum={result['sum']} "
          f"buckets={result['buckets']} late_recomputations={result['late_recomputations']}")
    for window in result["windows"]:
        sketch = window["sketch"]
        print(
            f"  dim={window['dim']} window=[{window['window_start']}, {window['window_end']}] "
            f"count={window['count']} sum={window['sum']} "
            f"p50={window['p50']:.2f} p95={window['p95']:.2f} p99={window['p99']:.2f} "
            f"recomputations={window['recomputations']} sketch_size={sketch['size']}"
        )


reset()

# 第一段：同一批次事件时间跨自然小时，按事件时间落到不同窗口
add({"dim": "shop", "ts": 100, "value": 2})
add({"dim": "shop", "ts": 3599, "value": 8})
add({"dim": "shop", "ts": 3600, "value": 100})
show("跨小时批次按事件时间切窗", query("shop"))

# 第二段：迟到数据落到历史窗口，触发该窗口整体重算（幂等）
add({"dim": "shop", "ts": 200, "value": 5})  # 迟到：水位线已推进到 3600
result = query("shop")
show("迟到数据触发历史窗口重算", result)
print(f"顶层草图状态: algorithm={result['sketch']['algorithm']} "
      f"size={result['sketch']['size']} total_weight={result['sketch']['total_weight']}")

# 第三段：层级维度，父维度上卷合并子维度草图
reset()
add({"dim": ("cn", "s1"), "ts": 0, "value": 1})
add({"dim": ("cn", "s2"), "ts": 0, "value": 99})
show("父维度 cn 上卷子门店", query(("cn",)))
