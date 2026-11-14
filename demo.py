from metricdedup import dedup, flush, put

# 第一段：重复上报只计数不写入
dedup.reset()
put({"name": "cpu", "ts": 1})
put({"name": "cpu", "ts": 1})
print("重复上报:", flush())

# 第二段：乱序到达，旧值不覆盖新值
dedup.reset()
put({"name": "cpu", "ts": 20})
put({"name": "cpu", "ts": 10})  # 乱序旧值，被丢弃
print("乱序保护:", flush(), "最新ts:", dedup._seen["cpu"])

# 第三段：窗口外清理（窗口 10s，事件时间滑动）
dedup.reset()
dedup.configure(window_seconds=10)
put({"name": "old", "ts": 1})
put({"name": "new", "ts": 100})  # 水位推到 100，old 过期淘汰
put({"name": "late", "ts": 5})   # 窗口外迟到数据，计过期不写入
print("窗口清理:", flush())
dedup.configure(window_seconds=60)

# 第四段：状态落盘，重启恢复
dedup.reset()
put({"name": "cpu", "ts": 30})
put({"name": "cpu", "ts": 30})
print("落盘前:", flush())
dedup.reset()          # 模拟重启：内存清空
dedup.load_state()     # 从落盘文件恢复
print("重启恢复:", flush())
