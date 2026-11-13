# pushdown

现状：每次推整批，没有水位、没有版本，token参数被忽略。

问题：下游重复处理，失败重跑从头来，分不清新数据和重放。

目标：按批次和水位增量推送，断点续推，下游确认后推进。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
