# tableaudit

现状：每条操作单独存一条，查询全量扫描。

问题：重复记录，敏感操作没有标记，下钻很慢。

目标：按表、操作人和类型聚合，窗口内去重，敏感操作留样本。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
