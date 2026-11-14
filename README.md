# lineagerepair

现状：推断接口返回空，确认不写回。

问题：血缘漏登记就断链，日志里的读写记录没人整理。

目标：从日志推断边并给置信度，人工确认写回并审计。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
