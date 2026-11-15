# fieldpatch

现状：缺字段的行原样返回，不补也不隔离。

问题：整行丢弃，默认值不分类型，修补无来源。

目标：按字段策略补默认、降级或隔离，记录来源和缺失率。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
