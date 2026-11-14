# retention

数据保留策略的级联删除模块，纯 Python 标准库。按天做保留判定，沿血缘级联
清理下游副本和缓存，活跃租约保护的分区跳过，删除分批提交并写审计。

## 保留口径

- 入口保持两个：plan(dataset, partitions, ...) 出计划，apply(plan, ...) 执行。
- 主表分区满足 age_days > keep_days（默认 30 天，等于保留期不算过期）进 delete。
- 计划里的分区字典原样保留，调用方可继续读取自定义字段。

## 级联口径

- 血缘两种来源：分区内联的 downstream，以及 plan(..., lineage=...)，键值形如
  ("table", "partition") -> [("down_table", "down_partition"), ...]，两者合并。
- 从每个待删主表分区出发 BFS，下游分区进 cascade，每项带 via 指向上游；
  环和重复边去重，因此不会重复删除。没有级联目标时结果不含 cascade 键。
- 活跃租约（lease 字段或 plan(..., leases=...)，显式 active: false 视为已释放）
  命中的分区进 skipped，reason="active_lease"，并记录 via/lease；该分区同时
  作为血缘屏障，其下游不在本轮遍历，租约释放后由下次清理处理，避免删到仍被引用的数据。

## 分批与失败

- 删除目标 = 主表过期分区 + cascade 下游，按 batch_size（默认 100）分批，
  批间按 interval_seconds 睡眠（默认 0，不睡眠），长清理可配置间隔避免久锁。
- 每批调用注入的 delete_func([ref, ...])；某批抛错即停在该批，已提交批次不回滚，
  改抛 RetentionError，其 partial 含失败前的批次、计数和审计。无删除目标时
  结果不含 batches 键。

## 审计与输出

- 传入 AuditLog(path=...) 可把审计按 JSON Lines 落盘，不传则只保存在内存。
- 审计依次记录 retention_start（完整范围 scope、跳过项及原因）、每批的
  batch_start / batch_committed（批次号、批大小、累计删除数），失败时记
  batch_failed（批次号、错误信息）。
- apply 返回删除分区、级联范围（计划中）、提交批次和跳过项。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
