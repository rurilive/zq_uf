# partcompact

现状：合并时先删旧文件再写新文件，没有清单。

问题：合并期间查询读不到，失败留半份，并发合并踩踏。

目标：读旧写新再原子切换，失败保留旧文件，重复合并幂等。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
