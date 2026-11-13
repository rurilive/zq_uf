# sitemaptool

现状：解析单个 urlset 文件，读取 loc、lastmod 和 changefreq。

问题：索引文件不递归，压缩包当文本解析，多站点没有优先级。

目标：递归入口发现、流式解压、去重和优先级排序。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
