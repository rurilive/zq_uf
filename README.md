# tablecopy2

快照加增量的表复制：一致性快照定版本，增量补偿复制期间变更，失败清理半成品并从断点续传，完成后校验行数与内容校验和。

## 流程

1. `snapshot()` 取一致性快照，快照位点（版本号）定住源表版本；
2. 分批复制快照数据，每批落检查点；失败只留暂存区（`query` 不可见），重跑从断点继续；
3. 按位点补偿复制期间的 insert/update/delete，幂等追平；
4. 原子发布目标表（立即可查），逐行比较行数与 sha256 内容校验和；
5. 不一致则停止并保留两侧数据，差异明细、两侧计数、快照位点均可查，`abort` 后才能重跑。

返回值含：`snapshot_position`（快照位点）、`caught_up`（补偿数量）、`diff`（校验差异）、`src_count`/`dst_count`（两侧计数）、`matched`、`resumed`。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
