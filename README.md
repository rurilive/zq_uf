# crawlflow

现状：先进先出队列、本地 SQLite、URL 完全相等去重、失败重试三次。

问题：URL 变体重复抓取，失败不分类，断点不能恢复，站点共用一个并发池。

目标：归一化去重、优先级、分类退避、断点恢复和站点隔离。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
