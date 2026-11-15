# tableaudit

表操作审计聚合模块，纯 Python 标准库。审计事件按 **(表, 操作人, 操作类型)**
在 **小时 / 天两级窗口**内聚合，窗口内按指纹去重；敏感操作单独标记并全量
保留原文；支持时间/表下钻、游标翻页、聚合回放与过期归档。

## 聚合口径

- `record(event)` 接口签名不变，每次只写入一条事件，返回仍为
  `{"recorded", "total", "aggregated": False, ...}`（旧断言保持不变：
  `aggregated=False` 表示本次调用写的是单条事件，聚合在存储/查询层完成）。
  额外返回 `fingerprint`、`duplicate`、`sensitive`、`operator_change`。
- 聚合键：`(table, user, op)` + 窗口粒度（`hour`/`day`，UTC 整点桶），
  同一事件同时进小时桶和天桶。
- 每个聚合体输出：`count`（总次数）、`unique`（不同指纹数）、
  `duplicates`（重复次数，`count = unique + duplicates`）、
  `first_seen/last_seen`、`samples`（不同指纹的事件样本，默认上限 3）。

## 去重口径（指纹）

- 指纹 = 剔除时间字段（`time`/`ts`）后 canonical JSON 的 sha256
  （`json.dumps(sort_keys=True)`）。
- 去重范围限定在同一聚合窗口与同一聚合键内；跨小时/跨天不去重。
- 重复事件不新增样本，只累加 `count` 与 `duplicates`。

## 敏感操作

- 默认敏感集合：`drop / truncate / delete / alter / grant / revoke`，
  可通过 `configure(sensitive_ops=...)` 覆盖。
- 敏感聚合体带 `sensitive=True` 标记，样本照留；**原文在独立存储中
  全量保留**（不受样本上限影响），查询传 `include_raw=True` 时随
  `raw` 字段返回，`raw_count` 始终可用。

## 查询口径

`query(filters, limit=None, cursor=None)`（参数也可写进 `filters`）：

- 精确匹配：`table / user / op / sensitive / granularity / bucket` 等
  聚合体字段；`granularity` 默认 `hour`，可传 `day`。
- 时间范围：`start`/`end`（epoch 秒，半开区间，按窗口相交判定）。
- 排序：按 `last_seen` **时间倒序**，同时间按窗口与聚合键稳定排序。
- 翻页：opaque base64 游标，返回 `hits`（旧键）、`cursor`/`next_cursor`
    （下一页游标，末页为 `None`）、`has_more`、`total`。
- 操作人变更随结果在 `operator_changes` 中返回；也可用
  `operator_changes(table, op)` 单独查。
- 下钻：`drill_down(table, day=None, user=None, op=None)` 返回该表
  （可限某天）的小时级聚合。

## 操作人变更

同一 `(table, op)` 的 `user` 发生变化时单独生成一条
`operator_change`（含 `from_user/to_user/time`），记录在独立列表，
不混入普通聚合体；保留期到期随聚合体一起归档。

## 保留期与归档

- `configure(retention_seconds=N, archive_dir=DIR)`：窗口结束超过 N 秒
  的聚合体与变更记录视为过期；未配置保留期则永久保留。
- `archive_expired(now=None)`：手动/按次触发归档（`record` 后会自动
  调一次），过期条目进入内存归档库并在配置目录追加 `audit-archive.jsonl`；
  敏感聚合的原文随归档一起保留。`read_archive()` 读取归档内容。

## 回放

`replay(filters=None)` 按时间正序重建事件流：小时粒度的敏感聚合回放
**全部原文**（`retained="full"`），其余聚合（含天粒度敏感聚合，原文
只存在小时桶）回放**样本**（`retained="sample"`），操作人变更以
`kind="operator_change"` 条目并入，可按表等条件过滤。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
