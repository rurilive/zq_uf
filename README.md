# surfacededupe → surfacemap

攻击面清单小工具，按入口合并域名、地址、端口和归属，带状态与来源。

## 行为

- `surface(entries)` 按 `(domain, address, port)` 合并：同一入口重复登记不新增行，
  合并来源、刷新确认时间；`domain` 大小写不敏感，`port` 默认 443。
- 入口记录 `source`（可能与 `source_seen` 记录每个来源首次出现时间）、
  `discovered_at`（最早发现时间）、`owner`、`third_party`。
- `offline=True` 或 `status="offline"` 把入口标成 `offline` 并保留 `offline_at`，**不删除**；
  之后不带 `offline` 再登记即恢复为 `active`，保留 `last_offline_at` 供追溯。
- 归属为空记为 `unknown`，留在待确认里；同一入口被登记到不同业务名下时
  进入 `owner_conflict` 待确认，`owner_candidates` 列出候选归属。
- `confirm(entries=None)` 刷新确认时间；`stale(days)` 列出长期未确认的入口（默认 90 天）。
- `changes(month)` 按月导出新增、下线、恢复和归属冲突，桶内是变化时的快照，可单独导出。

## 返回结构

`surface()` 返回本次 `added`/`updated` 及合并后的 `total`、`active`、`offline`、
`pending`、`third_party`、`sources` 和各类 `*_entries`。

`report()` 返回 `total`、`active`、`offline`、`pending`、`third_party`、`stale`、
`by_owner`（按业务的有效入口分组）、`groups`、`sources`、全部 `entries`、
`active_entries`/`offline_entries`/`pending_entries`/`third_party_entries`/`stale_entries`，
以及当月的 `changes`。`report(owner=...)` 按业务筛选，`report(status=...)` 筛选状态。

`surface()` 和 `report()` 的调用方式保持不变，`now=` 仅用于测试时固定时间。
