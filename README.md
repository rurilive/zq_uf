# multisource

现状：只取前两份数据按主键比较，差异分缺失和值变化两类。

问题：第三个系统被忽略，口径不同直接比会误报，差异没有归因。

目标：多源对齐，字段级口径转换，按来源和类型归因，报告可下钻。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
