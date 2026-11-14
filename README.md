# retention

现状：按年龄列出过期分区，一次性删除。

问题：下游副本和缓存还在，删维表事实表对不上，大删锁表。

目标：沿血缘级联，跳过活跃租约，分批提交并审计。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
