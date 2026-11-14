# tablettl

现状：按年龄挑过期分区，直接删除。

问题：下游还在读，归档缺失，大删锁表。

目标：先归档再删，下游确认，分批和失败停批。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
