# dataprof

现状：所有行留在内存，画像时对全量数据重算。

问题：数据量一大就爆内存，异常阈值写死，画像不随数据版本更新。

目标：按批增量更新，分位数用草图，异常按分位和倍数判定。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
