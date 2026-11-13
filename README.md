# cdcmerge

把数据库变更流合并成内存快照，纯 Python 标准库。入口：`Merger.apply(events)`
应用一批变更并返回当前快照，`Merger.snapshot()` 只读取当前快照。

## 事件格式与合并规则

每条变更带 `key`（主键）、`version`（版本号）、`txn`（事务号）和 `seq`
（事务内序号），`op` 为 `insert`/`update`/`delete`；事务结束事件为
`{"op": "commit", "txn": 事务号}`。

- 版本收敛：同一主键只保留最高 `version` 的变更，乱序到达也按版本比较，旧版本丢弃。
- 墓碑：`delete` 不直接移除记录，而是保留带版本的墓碑；晚到的旧版本变更不能复活记录，只有更高版本的 `insert`/`update` 才能让主键回归。
- 幂等：按 `(txn, seq)` 识别重复事件，重复的变更和 `commit` 直接忽略；无 `txn`/`seq` 的旧格式事件按版本规则立即生效。
- 事务原子性：带 `txn` 的变更先缓冲，`snapshot()` 不暴露未提交事务；收到对应 `commit` 后整个事务一次性合并可见，不同事务互不影响。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
