# aggretract

现状：按主键累加，只增不减。

问题：更新和撤回不生效，重复消费翻倍，迟到数据被丢。

目标：版本覆盖、撤回扣减、迟到窗口和对账字段。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
