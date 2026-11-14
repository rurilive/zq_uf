# rowlineage

现状：按任务记录输入输出表，主键映射丢弃，查询只到表级。

问题：不知道某行来自哪些输入行，更新传播不到下游，有环时无限展开。

目标：主键级边，增量幂等传播，环路截断，输出路径和层级。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
