# crawlreport

现状：只统计总行数，失败和跳过混在一起。

问题：报告和实际入库对不上，按站点和时间下钻不了。

目标：按站点、状态和阶段统计，报告与入库行数对账。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
