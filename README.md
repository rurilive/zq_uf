# smallfile

现状：所有分区一次重写，不看文件大小。

问题：查询打开太多文件，合并覆盖在写分区，清单不同步。

目标：按大小选分区，活跃租约跳过，原子切换并更新清单。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
