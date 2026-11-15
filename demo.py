"""演示：乱序版本、迟到消息、窗口清理、落盘恢复与统计。

运行：python3 demo.py
"""

import os
import tempfile

from metricdedup2 import configure, flush, put, reset, stats


def show(label, result):
    print(
        f"{label:<26} accepted={result['accepted']!s:<5} "
        f"reason={result['reason']:<10} key={result['key']:<5} "
        f"version={result['current_version']} size={result['size']} "
        f"watermark={result['watermark']}"
    )


print("== 1. 乱序版本：同键保留最高版本 ==")
reset()
configure(state_path=None, window=60.0)
show("v3 先到", put({"key": "pv", "version": 3, "event_time": 100, "value": 10}))
show("v2 迟到", put({"key": "pv", "version": 2, "event_time": 99, "value": 8}))
show("v4 再到", put({"key": "pv", "version": 4, "event_time": 101, "value": 12}))
show("v4 重复", put({"key": "pv", "version": 4, "event_time": 101, "value": 12}))
show("v1 迟到", put({"key": "pv", "version": 1, "event_time": 98, "value": 5}))
print(f"最终状态：{stats()}")

print("\n== 2. 事件时间窗口：水位推进清理旧状态 ==")
reset()
configure(state_path=None, window=10.0)
put({"key": "old", "version": 1, "event_time": 100})
put({"key": "mid", "version": 1, "event_time": 105})
show("晚到事件 120", put({"key": "new", "version": 1, "event_time": 120}))
print(f"窗口后：{stats()}")
show("105 迟到（超窗）", put({"key": "late", "version": 9, "event_time": 105}))
print(f"flush 强制水位到 1000：{flush(watermark=1000)}")

print("\n== 3. 落盘与恢复 ==")
state_dir = tempfile.mkdtemp(prefix="metricdedup2-demo-")
configure(state_path=state_dir, window=10.0)
put({"key": "a", "version": 2, "event_time": 200})
put({"key": "b", "version": 1, "event_time": 205})
flush()                                  # 全量快照
put({"key": "c", "version": 1, "event_time": 208})  # 增量日志
before = stats()

configure(state_path=None)               # 丢掉内存，模拟重启
configure(state_path=state_dir)          # 从快照 + 日志恢复
after = stats()
print(f"重启前：size={before['size']} watermark={before['watermark']}")
print(f"重启后：size={after['size']} watermark={after['watermark']}")
show("恢复后继续", put({"key": "d", "version": 1, "event_time": 209}))
print(f"恢复并推进后：{stats()}")
print(f"快照文件：{os.listdir(state_dir)}")
