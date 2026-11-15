# smallfile

现状：所有分区一次重写，不看文件大小。

问题：查询打开太多文件，合并覆盖在写分区，清单不同步。

目标：按大小选分区，活跃租约跳过，原子切换并更新清单。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现说明）
- `plan(dataset, only=[...])`：按分区统计文件数/大小，平均小于阈值才选入；活跃读取、文件够大、非试运行分区都跳过并给出原因。
- `compact(plan)`：合并写新文件后整体替换并同步更新清单；原文件进 history 保留一段时间；失败保留原文件，返回合并/跳过/替换/失败清单和前后文件数对比。
- `retry_failed` 单独重试失败批次，`rollback` 按批次回退，`cleanup_history` 清理过期历史。
