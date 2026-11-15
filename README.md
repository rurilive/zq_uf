# filelineage

现状：只记录任务读了哪些文件，不建文件之间的派生边。

问题：影响面查不到下游文件，多跳看不到，血缘断链。

目标：按文件读写建边，多跳查询带层级和路径，环路截断。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
