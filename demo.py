"""演示：事件时间窗口 + 迟到触发重算（撤销旧值 -> 全量重算 -> 写新值）。

运行：python3 demo.py
状态落盘目录：./demo_state（可用 BATCHWINDOW_STATE_DIR 覆盖）。
"""

import os

from batchwindow import (
    assign,
    configure,
    get_window,
    late_events,
    recompute,
    recompute_records,
    reset,
    windows,
)

STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_state")

reset(state_dir=STATE_DIR)
configure(state_dir=STATE_DIR, window_size=10, allowed_lateness=0)

print("== 1. 正常事件按事件时间落窗（窗口大小 10s）==")
for event in (
    {"id": "e1", "ts": 2, "value": 100},   # 窗口 0: [0, 10]
    {"id": "e2", "ts": 8, "value": 50},    # 窗口 0
    {"id": "e3", "ts": 12, "value": 7},    # 窗口 1: [10, 20]
    {"id": "e4", "ts": 25, "value": 3},    # 窗口 2，水位线推进到 25
):
    print("assign ->", assign(event))

print("\n== 2. 迟到事件到达（ts=3 属窗口 [0,10]，end=10 < 水位线 25，窗口已关）==")
late_event = {"id": "e5-late", "ts": 3, "value": 25}
print("assign ->", assign(late_event))

print("\n== 3. 手动重算幂等：连续两次，old_value == new_value，不累加 ==")
print("recompute #1 ->", recompute(0))
print("recompute #2 ->", recompute(0))

print("\n== 4. 全部窗口元数据（闭区间 + 聚合值）==")
for meta in windows():
    print(meta)

print("\n== 5. 迟到记录 ==")
for record in late_events():
    print(record)

print("\n== 6. 按窗口 / 区间查询重算记录（对账用，含旧值和新值）==")
print("window=0 ->", recompute_records(window=0))
print("range[0,1] ->", recompute_records(start_window=0, end_window=1))

print("\n窗口 0 当前状态 ->", get_window(0))
print("落盘目录 ->", os.path.abspath(STATE_DIR))
