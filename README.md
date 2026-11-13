# idemwrite

入库前的幂等写入层（纯 Python 标准库），接口保持 `upsert(rows, keys)`，
存储载体仍是普通 `dict`：`upsert` 返回的对象与老代码完全兼容。

## 版本规则

行内用 `version` 字段表示版本，写入前按业务键查现有版本再决定动作：

- 新版本（`incoming > current`）：覆盖，计入 `updated`；键不存在则插入，计入 `inserted`。
- 同版本（`incoming == current`）：视为重复插入/重试，跳过并计入 `duplicates`，数据不变。
- 旧版本（`incoming < current`）：版本回退，忽略并计入 `stale`，数据不变。
- 行内无 `version` 或存量数据无版本：按最后写入生效，兼容旧调用方。

统计通过 `get_stats(store)` 读取，`reset_stats(store)` 清零；存储弃用时调用
`drop_store(store)` 清理内部注册表。

## 分批与重试

`upsert(rows, keys, store, batch_size=100)` 按 `batch_size` 分批顺序提交，
批次内任意一行失败都会原子回滚整批并抛 `BatchWriteError`，前面已提交的批次
不受影响。直接对同一个 `store` 重跑整批数据即可恢复：已成功的行按同版本
重复跳过，失败批次重新提交，不会产生重复数据。

## 并发冲突处理

每个业务键有独立锁，按键排序后统一加锁避免死锁。写入采用条件更新：
持锁后重读版本再判定，若与判定前不一致（并发写先提交）则整批重读再判、
自动重试，最多 `MAX_COMMIT_ATTEMPTS` 次，超出抛 `WriteConflict`（包装为
`BatchWriteError`）。唯一键冲突不再直接失败，而是转成版本比较：新版本胜出，
旧版本忽略并计数，因此并发写同一业务键不会出现版本回退。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
