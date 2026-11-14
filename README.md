# partitionmap

现状：分区按数据集加分区名固定落盘。

问题：扩容要全量迁移，规则冲突覆盖，迁移期间双路径。

目标：规则路由，优先级，双读切换和回滚。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
