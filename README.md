# auditsearch

现状：全部审计一次读进内存，检索只做精确匹配，游标用偏移量。

问题：几百万条内存放不下，翻页会重复或漏，结果没有排序。

目标：流式过滤，游标记录位置和校验，翻页不重不漏。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
