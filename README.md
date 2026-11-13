# latejoin

现状：维表按主键取最新，事实逐条关联。

问题：迟到维表不回补，多版本被覆盖，空键整行丢。

目标：事件时间多版本、迟到回补、空键保留和匹配统计。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
