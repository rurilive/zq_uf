# snapver

现状：创建快照时整目录复制，回滚直接覆盖。

问题：复制到一半的文件也进快照，没有元数据，重复占用空间。

目标：一致视图、清单校验、内容去重和校验失败拒绝回滚。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

目录结构：`store/objects/<sha256>` 存去重后的文件内容，`store/snapshots/<tag>.json`
记录清单（路径、大小、校验和、创建时间），`store/refs.json` 记录每个对象的引用计数。
创建快照时跳过 `*.tmp`/`*.swp` 等临时文件；相同内容只存一份，引用计数归零才删除对象。
回滚前逐个校验对象存在且 sha256 与清单一致，失败抛 `SnapshotError` 且不改数据目录；
`restore(tag)` 返回实际恢复的文件数，`delete(tag)` 释放引用。
