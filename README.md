# attacharchive

现状：整文件读入后直写，没有分片和清单对账。

问题：大附件爆内存，断连重来，清单和对象不一致。

目标：分片校验加原子合并，清单对账并只补差异。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
