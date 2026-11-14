# crawljournal

现状：按行追加JSON，没有长度和校验，恢复遇到半行就报错。

问题：崩溃后半行让整段解析失败，坏块发现不了，截断靠人工。

目标：带序号和校验的预写日志，恢复时丢弃半行并可定位。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
