# proxycheck

现状：逐个出口探测一次连通性，挑第一个可用的。

问题：抖动出口留在池里，慢出口拖任务，坏出口不会摘除。

目标：多次探测、成功率打分、摘除与恢复、并发与告警。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
