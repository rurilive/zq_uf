# incbackup

纯 Python 标准库的内容哈希增量快照备份。块按 SHA-256 去重，只在内容首次出现时
写盘；快照用清单记录块引用和父快照，恢复时沿父链整链校验，支持任意点恢复，
链断裂或块损坏一律抛错拒绝。

## 接口

- `backup(root, dest="backups") -> dict`：对 `root` 做一次增量快照。
- `restore(tag, dest="restore", source="backups") -> dict`：校验父链后恢复指定标签。

返回字段：

- 备份：`tag`、`path`（快照物化视图目录）、`files`、`new_blocks`（本次新写块数）、
  `reused_blocks`（命中已有块的引用次数）、`parent`（父标签，首快照为 `None`）。
- 恢复：`tag`、`path`、`files`（恢复文件数）、`chunks_verified`（整链校验的块引用数）、
  `chain`（旧到新的标签序列）。

## 仓库布局与块格式

```text
dest/
  TAGS.json                    # 标签索引：version + tags 映射（标签 -> 清单相对路径）
  blocks/<哈希前2位>/<sha256>   # 内容寻址块，文件内容即原始字节，无额外封装
  snap-NNNN/SNAPSHOT.json      # 快照清单
  snap-NNNN/tree/...           # 该快照的物化文件视图（单块文件硬链接到 blocks）
```

块规则：

- 哈希算法：SHA-256，块文件名即内容的十六进制摘要，天然全局去重。
- 分块阈值：`CHUNK_SIZE = 65536`（64 KiB），文件按该大小顺序切块，最后一块可更短。
- 空文件不产生任何块，只在清单中记录空的 chunks 列表。
- 快照清单逐文件记录 `path`、`chunks`（有序哈希列表）、`size`、权限 `mode`；
  目录记录在 `directories`；符号链接以 `symlink` 记录目标路径，不跟随。
- 多块文件按清单顺序拼接还原；物化视图优先硬链接（零拷贝），跨设备时退回复制。

## 恢复口径

1. 读取 `TAGS.json`，未知标签、索引缺失或损坏直接抛 `RestoreError`。
2. 从目标标签沿 `parent` 走到根快照：任一清单缺失、损坏、标签不一致或出现环，
   判为链断裂，拒绝恢复。
3. 对链上所有清单引用的每个块检查存在性并重算 SHA-256；缺块或哈希不符即拒绝。
4. 全部校验通过后才清空并重建 `dest`，按目标快照清单物化文件，因此不存在
   半截恢复；异常路径全部显式抛错，不静默跳过。

## 异常

- `BackupError`：备份根目录无效、标签索引损坏、父快照清单缺失、遇到不支持的文件类型。
- `RestoreError`（`BackupError` 子类）：未知标签、仓库/索引缺失、父链断裂或成环、
  块缺失或哈希校验失败、清单损坏。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

`demo.py` 做三次备份（首写、无变化复用、单文件改动）再恢复第三个快照，输出的
`new_blocks`/`reused_blocks`/`files`/`chain` 与 `tests/test_backup.py` 的断言口径一致。
