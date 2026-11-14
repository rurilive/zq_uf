# lineageimpact

现状：只记直接边，查询一跳。

问题：多跳看不到，字段级没建，查询无缓存。

目标：多跳和字段级，环路截断，缓存失效。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
