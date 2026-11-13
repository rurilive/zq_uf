# datasearch

现状：对表名做区分大小写的子串匹配，不排结果。

问题：描述和字段搜不到，同义词和缩写不识别，最相关的排后面。

目标：索引表名、描述、字段和标签，支持同义词和前缀，按相关度排序。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
