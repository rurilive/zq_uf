# tablecopy

现状：一次性全量复制，只比行数。

问题：复制期间源表写入丢数据，失败留半表，无校验。

目标：快照加增量，失败清理，断点重试和校验和。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
