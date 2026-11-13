# reconcheck

现状：比较两张表的主键集合和行数。

问题：金额差一分发现不了，重复主键被吞，只有通过与否。

目标：组合键、精度误差、分区并行、例外规则和差异样本。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
