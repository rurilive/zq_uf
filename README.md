# partgc2

现状：不在保留列表里的分区一次删除。

问题：不看活跃租约，删到正在读的分区，和备份并发踩踏。

目标：回收前检查租约，并发备份的新分区先登记，删除分批。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
