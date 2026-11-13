# logparse

现状：按空格切分日志，取时间、级别和剩余文本。

问题：字段带空格就错位，多行堆栈丢掉，时区不统一。

目标：多格式识别、转义、堆栈归并、UTC 和失败保留。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
