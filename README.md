# robotsdiff

robots 规则变更的影响分析模块（纯 Python 标准库）：比较新旧规则的允许与禁止段，
输出差异分类，只定向失效受影响链接的判定缓存，并把每次变更写入可回溯的审计记录。

## 规则与判定口径

- 规则形如 `{"allow": ["/pub"], "disallow": ["/private"]}`，段内为路径前缀，
  按 `path.startswith(prefix)` 命中；空串前缀忽略，段内自动去重排序。
- 判定顺序：allow 命中优先于 disallow，均未命中默认放行。
- `allowed(host, path)` 的判定结果按路径缓存；规则更新时只删除判定翻转的缓存项。

## 差异分类口径

`update(host, new_rules, source=None, fetched_at=None, summary=None)` 比较新旧规则，
`diff(host)` 返回最近一次变更的完整分类：

| 字段 | 含义 |
| --- | --- |
| `newly_disallowed` | 新规则新增的禁止前缀（收紧） |
| `lifted_disallowed` | 新规则移除的禁止前缀（放宽） |
| `newly_allowed` | 新规则新增的允许前缀 |
| `removed_allow` | 新规则移除的允许前缀 |
| `affected_paths` | 缓存中因本次变更判定翻转的具体路径 |

`affected(host)` 只返回 `affected_paths`，即定向失效清单；此前从未缓存过的路径
不在其中（下次 `allowed` 直接按新规则判定），未翻转路径的缓存原样保留。

## 失效口径

更新时逐条对比已缓存路径在新旧规则下的判定：仅当结果翻转（允许→禁止或禁止→允许）
时删除该缓存项。审计记录的 `invalidation` 同时给出失效条数与保留条数。

## 审计与回滚

每次 `update` 与 `rollback` 都向 `audit(host)` 追加一条记录，包含：

- `time`：变更处理时间（UTC ISO 8601）
- `source`：规则来源（如 robots.txt 的 URL）、`fetched_at`：规则抓取时间
- `summary`：规则摘要（allow/disallow 条数 + 内容 sha256），便于回溯比对
- `diff`：本次差异分类、`invalidation`：定向失效明细、`rules`：生效规则快照

`rollback(host)` 恢复上一版规则（首次更新后回滚则恢复为默认放行的空规则），
同样执行差异分析与定向失效，并追加 `action="rollback"` 审计，携带被恢复版本的
来源与抓取时间；无历史时抛出 `KeyError`。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
