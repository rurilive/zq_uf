# backfillplan

现状：整段回填只生成一个分片，顺序执行。

问题：大任务跑几天，失败从头来，并发度固定。

目标：按时间主键切片，动态并发，断点重试和幂等。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
