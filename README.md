# fileindex

数据文件的内存指纹索引，纯 Python 标准库实现。按文件大小和分块 SHA-256
建立指纹，支持目录级增量更新、删除清理和变化统计。接口保持 `index(root)`
与 `changed(path)`，另有 `reset()` 清空内存索引。

## 索引格式

索引为内存字典 `_index`，键是文件绝对路径，值为一条记录：

- `size`：文件字节数（`os.stat`）。
- `mtime`：修改时间浮点秒（`os.stat`）。
- `chunks`：按 `CHUNK_SIZE` 分块的 SHA-256 十六进制摘要列表，空文件为单条空内容摘要。

## 增量规则

`index(root)` 每次扫描 `root` 下全部文件，按下列规则分类计数：

1. 新文件（无记录）：计算指纹后记入索引，计入 `added`。
2. 大小与 `mtime` 都未变：不读文件，直接计入 `skipped`。
3. 仅 `mtime` 变化、大小不变：重算分块哈希；哈希相同则只刷新 `mtime` 并计入
   `skipped`（拷贝导致的时间变化不重跑），哈希不同计入 `changed`。
4. 大小变化：重算指纹并计入 `changed`。
5. 扫描根目录下有记录但本轮不存在的文件：删除记录并计入 `deleted`；
   根目录之外的记录不受影响，因此可对子目录单独增量更新。

返回统计字典：`added`/`changed`/`deleted`/`skipped` 为本次分类计数，
`indexed` 为本轮实际扫描到的文件数，`total` 为索引当前总记录数。

`changed(path)` 是轻量查询：未建索引、大小或 `mtime` 与记录不一致即返回
`True`，不做全量哈希；内容变化但大小和 `mtime` 都不变的极端情况不会被它发现，
会在下一次 `index()` 重算分块哈希时识别（需要 `mtime` 或大小先发生变化才会触发重算）。

## 参数与阈值

- `CHUNK_SIZE`（`fileindex/index.py`，默认 `1048576` 即 1 MiB）：分块大小。
  文件按该大小切块逐块哈希；大小恰好是块大小整数倍的文件产生 N 个块，
  再多 1 字节即多出一个块，空文件固定 1 条空摘要。
- `index(root)` 的 `root` 必须是已存在的目录；无返回值参数，结果在内存索引中。

## 错误处理

任何异常路径都抛出 `fileindex.FileIndexError` 并带具体路径与原因，绝不静默跳过：

- `root` 不存在或不是目录。
- 遍历目录、获取文件状态或读取文件内容失败（含权限问题）。
- `changed(path)` 指向的文件不存在或无法获取状态。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

`demo.py` 连续两次扫描同一目录：第一次全部为新增；第二次制造“仅改时间、
改内容、删除、新增”四种情况，输出与 `tests/test_index.py` 中断言一致：

- 第一次：`added=3, changed=0, deleted=0, skipped=0, indexed=3, total=3`。
- 第二次：`added=1, changed=1, deleted=1, skipped=1, indexed=3, total=3`。
