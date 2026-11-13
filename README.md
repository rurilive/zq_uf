# fileindex

数据文件的增量指纹索引：大小 + 修改时间快速判断，分块哈希做指纹，纯标准库。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 索引格式

- `_index` 以文件绝对路径为键，值为 `{"size", "mtime_ns", "hash"}`。
- `hash` 是 sha256，按 `chunk_size` 分块流式计算，大文件不全量读入内存。

## 增量规则

- `index(root)` 可重复调用：`size` 与 `mtime_ns` 都未变的文件跳过，不重复算哈希。
- 新文件计入 `added`，指纹重建计入 `changed`，未变计入 `skipped`。
- 扫描后 `root` 下已消失的文件记录同步清理，计入 `deleted`（只清理本次扫描的 root 范围）。
- 返回统计：`{"added", "changed", "deleted", "skipped", "total"}`。
- `changed(path)` 单文件快速校验：未索引或已删除视为变化；`size`/`mtime_ns` 任一不同即变化。

## 参数与阈值

- `index(root, chunk_size=65536)`：分块大小默认 64 KiB，必须为正整数。
- 比较键为 `(size, mtime_ns)`，纳秒精度；内容变但两者都不变的极端情况需 `reset()` 后重建。

## 错误处理

- `root` 不存在 → `FileNotFoundError`；不是目录 → `NotADirectoryError`。
- 非常规文件（坏软链、fifo 等）或读写失败 → 带路径的 `OSError`，不静默跳过。
- `changed` 对既未索引又不存在的路径 → `FileNotFoundError`。
