# tablecopy2

现状：直接全量复制，不取快照也不校验。

问题：复制期间写入漏数据，失败留半张表。

目标：快照加增量复制，失败清理并续传，校验行数和校验和。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
