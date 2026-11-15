# profiler2

现状：所有行留在内存，画像时全量重算。

问题：数据量大就爆内存，阈值写死，画像不随版本更新。

目标：按批增量，分位数用草图，异常按分位判定并落盘。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
