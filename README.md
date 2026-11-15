# crawlindex

现状：重建时先清空旧索引，再逐条写入，最后直接切换。

问题：重建期间查询不可用，没有校验，增量更新会被覆盖。

目标：新索引校验后原子切换，期间旧索引继续服务。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
