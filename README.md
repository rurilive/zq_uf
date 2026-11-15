# rowfold

现状：主键相同只留第一条，完整行可能被丢弃。

问题：同实体不同主键重复，取舍没有来源。

目标：主键加相似度折叠，按完整度选主行，保留取舍原因。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
