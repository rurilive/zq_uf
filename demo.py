from downsample import resample, rollup

# 分钟级数据：ts=180 处有一个尖刺，600~1199 是一段数据缺口
POINTS = [
    {"ts": 0, "v": 10},
    {"ts": 60, "v": 12},
    {"ts": 120, "v": 11},
    {"ts": 180, "v": 500},
    {"ts": 240, "v": 13},
    {"ts": 1200, "v": 9},
    {"ts": 1260, "v": 10},
]

print("== 10 分钟窗口（补空窗口，尖刺保留在 max，缺口单独统计）==")
for window in resample(POINTS, {"every": "10m", "fill": "pad"}):
    print(window)

print("== 多级降采样：10m -> 1h 与直接按 1h 计算一致 ==")
hourly = resample(POINTS, "1h")
staged = rollup(resample(POINTS, "10m"), "1h")
for direct, rolled in zip(hourly, staged):
    assert direct["start"] == rolled["start"]
    assert direct["count"] == rolled["count"]
    assert direct["min"] == rolled["min"]
    assert direct["max"] == rolled["max"]
    assert direct["last"] == rolled["last"]
    assert abs(direct["mean"] - rolled["mean"]) < 1e-9
print(hourly)
