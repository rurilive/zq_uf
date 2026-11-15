# tablelineage2

现状：每次记录都清空边表再全量写入。

问题：新增任务不增量更新，删除任务不清边，环路不检测。

目标：按任务指纹增量维护，删除只移除该任务的边，环路截断。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
