# metriclate2

现状：迟到事件直接累加到当前窗口，重算只回读。

问题：历史值一直错，重算重复累加，跨窗对不上。

目标：按事件时间整窗重算，先撤销再写，重算幂等并保留版本。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

实现：事件按 event_time 归窗（小时粒度），迟到事件回到历史窗口并触发整窗重算。
重算从事件日志算新值，在同一临界区内撤销旧版本、写入新版本，失败则旧版本不动。
每次重算生成一个版本，记录撤销的旧值，versions(window) 可查明细。
add/recompute 返回 window、recomputed、retracted、version。
demo 第二段演示先在一个历史窗口与人工核对，一致后再扩大范围。
