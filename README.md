# metricbackfill

现状：整段回填一个分片，直接累加。

问题：跑几天，重复回填翻倍，没有对账。

目标：分片回填，撤销重写，幂等和对账停批。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
