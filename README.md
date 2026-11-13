# reqcache

现状：以URL字符串为键的内存字典，没有过期，没有容量限制。

问题：POST和不同头也命中同一键，页面更新后读旧值，大响应撑爆内存。

目标：键含方法和关键头，按状态码过期，字节容量LRU，多进程共享索引。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
