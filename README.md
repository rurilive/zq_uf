# detailcrawl

现状：按 URL 列表逐个抓详情页，保持输入顺序。

问题：每条单独建连接，同模板重复解析，404 也重试。

目标：连接复用、模板分组、错误分类和批次明细。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
