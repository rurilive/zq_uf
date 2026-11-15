# partretention

现状：按天列出过期分区，一次全部删除。

问题：下游分区还在，删除不看依赖，大删一次锁表。

目标：沿血缘级联，跳过活跃租约，分批删除且可回滚。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
