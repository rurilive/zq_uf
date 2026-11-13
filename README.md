# paginate

现状：从第一页开始递增，遇到空页停止。

问题：起始页写死，总页变化仍按旧值翻，空页被当异常。

目标：页码与游标双模式、滑动窗口去重、断点恢复和并发排序。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
