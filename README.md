# surfacemap

攻击面清单小工具，登记域名、地址、端口和归属。

入口按「域名 + 地址 + 端口」合并成一条，重复登记只补充来源与确认时间；
下线入口标为失效、保留 `offline_at` 不删除，重新登记可恢复。归属缺失或彼此
矛盾的入口进入待确认；第三方托管入口单独标注；超过 `stale_days` 未重新确认的
入口在 `report()["stale"]` 中单列。每次变化写入变更日志，按月筛选/导出。

## API

- `surface(entries)` — 登记一批入口，返回 `added / merged / offline / restored / total`
- `report(owner=None, month=None, as_of=None, stale_days=90)` — 返回
  `active / offline / pending / third_party / stale / sources / by_owner / entries / changes`
- `changes(month=None)` / `monthly_changes()` / `export_changes(path=None, month=None)` — 变更日志
- `reset()` — 清空清单与变更日志

入口字段：`domain`、`address`、`port`(默认 443)、`owner`、`source`、`offline`、
`third_party`、`seen_at`(本次确认时间)、`first_seen`(最早发现时间，可选)。
