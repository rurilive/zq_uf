# fieldmerge

现状：按字段逐个覆盖，不同来源可以拼成一行。

问题：拼出的记录自相矛盾，可信度和时间都不看，冲突无解释。

目标：整行一致，权重加时间衰减，候选和胜出来源可查。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
