# etlconf

现状：按配置重命名字段，其余原样保留。

问题：空值和零值处理不一致，规则散落，新增数据源要复制脚本。

目标：可组合步骤、空值区分、规则冲突检测和按源覆盖。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
