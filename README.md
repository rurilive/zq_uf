# auditquery

现状：审计全量进内存，精确匹配，偏移分页。

问题：几百万条内存放不下，翻页重复或漏，结果无序。

目标：流式过滤，游标带指纹，翻页不重不漏并按时间排序。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
