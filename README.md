# cdcbatch

变更批处理模块（纯 Python 标准库）。带批次号与位点的**原子、幂等**写入：
整批成功才推进位点，失败按反向顺序回滚已应用行，重复批次按位点跳过，
冲突保留两侧原始值，全部状态落盘，失败批次可人工修复后续跑。

## 批次与位点口径

- 每个批次包含：`id`（业务令牌）、`seq`（批次号，整数）、`pos`（起始位点）、
  `rows`（每行自带严格递增的整数位点 `pos`）、`checksum`（位点校验和，
  由 `compute_checksum(batch)` 生成，覆盖批次号、位点与每行内容）。
- 引擎维护已提交水位线 `watermark`：首批 `pos` 必须为 0，之后每批的 `pos`
  必须等于 `watermark + 1`，位点不连续直接抛 `PositionGap`，防止错位写入。
- **整批成功才推进**：批次内所有行应用成功后，水位线一次性更新为末行位点，
  批次号与校验和登记到 `state.json`。
- **失败回滚**：任一行失败即停止，按应用的**反向顺序**撤销本批已写入的行
  （新建行删除、覆盖行恢复旧值），水位线不推进；原始批次写入
  `pending/<token>.json`，等待人工续跑。
- **幂等**：相同批次号 `seq` 重复提交时整批按位点跳过（`skipped` 计数），
  不重复写；同批次号但内容/校验和变化会抛 `ChecksumMismatch`。
- **冲突**：同表同 key 的字段值不一致时不覆盖，`current`/`incoming` 两侧
  原始值都保留并追加到 `conflicts.jsonl`，冲突统计按**表和字段**输出。
  值完全一致的行按幂等跳过处理。
- 批次大小通过 `BatchProcessor(state_dir, batch_size=N)` 配置，超限抛
  `BatchTooLarge`。
- 每次 `apply`/`resume` 的结果追加写入 `audit.jsonl` 审计日志。

## 用法

```python
from cdcbatch import BatchProcessor, compute_checksum

proc = BatchProcessor("./state", batch_size=100)

rows = [
    {"pos": 0, "table": "users", "key": "u1", "values": {"name": "alice"}},
    {"pos": 1, "table": "users", "key": "u2", "values": {"name": "bob"}},
]
batch = {"id": "b1", "seq": 1, "pos": 0, "rows": rows}
batch["checksum"] = compute_checksum(batch)

proc.apply(batch)     # 整批原子提交；失败返回 rolled_back=True 并落盘 pending
proc.resume("b1")     # 人工续跑：pending 修复后重试，已提交批次则按位点跳过
```

结果字典字段：`committed / applied / rolled_back / rolled_back_rows /
rolled_back_keys / skipped / conflicts / conflict_stats / watermark / resumed`。

落盘布局：

```
state/
  state.json          # watermark + 已提交批次（seq/pos/checksum），自带 digest
  audit.jsonl         # 应用与回滚审计
  conflicts.jsonl     # 冲突两侧原始值
  pending/<hash>.json # 失败批次原文，人工改写后用 resume(token) 续跑
  data/<table>.json   # 表数据
  .lock               # 文件锁，串行化同目录上的并发进程
```

## 兼容接口

模块级 `apply(batch)` / `resume(token)` 保留旧的逐条应用语义，供不带位点的
历史批次使用（失败不回滚）；携带 `pos` 的新格式批次会自动路由到进程内默认
持久化引擎。新代码请显式使用 `BatchProcessor`。

## 演示与测试

```bash
python3 demo.py                         # 模拟半途失败、反向回滚、人工续跑、幂等、冲突
python3 -m unittest discover -s tests   # 16 个用例（含原有断言，未改动）
```
