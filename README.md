# detectsoft

现状：状态码 200 一律判为正常页面，其余判为软 404。

问题：空页和占位页入库，模板级软 404 发现不了。

目标：状态、内容和模板一起判定，按站点与模板统计比例。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
