# csvrepair

现状：按逗号切分，列数不足补空串，写出repaired文件。

问题：引号内逗号和字段内换行被切错，原始行不留，报告也没有。

目标：按schema定位异常，能定的重切，不能定的整行保留并标记。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
