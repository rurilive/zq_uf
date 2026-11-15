# crawlguard

采集任务准入模块（纯 Python 标准库）。接口保持 `admit(task)` 与 `reload()`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 准入规则

`admit(task)` 按顺序校验，先命中先拒绝，结果里带 `decision`（`admit`/`reject`）、
`reason`、`reason_text`、`fingerprint` 和当时生效的 `list_version`：

1. 任务不是映射 → `invalid_task`
2. 来源命中 `sources.deny` → `source_denied`
3. 配置了 `sources.allow` 且来源不在其中 → `source_not_allowed`
4. 站点命中 `sites.deny` → `site_denied`
5. 配置了 `sites.allow` 且站点不在其中 → `site_not_allowed`
6. 指纹已准入过 → `duplicate`

黑名单优先于白名单；`allow` 为空表示不限制来源/站点。匹配用小写 `fnmatch`
通配（如 `cms-*`、`*.internal`）。

指纹口径：任务显式 `fingerprint` 优先；否则 `source + site + url` 的
SHA-256；无 `url` 时用 `source + site + id`。只有准入成功的任务才登记指纹，
被拒任务不占指纹，修好来源/名单后可重新提。

## 名单与热更新口径

名单是一个 JSON 文件：

```json
{
  "version": "2026-11-15.1",
  "effective_at": "2026-11-15T00:00:00Z",
  "sources": {"allow": ["cms-*"], "deny": ["cms-rogue"]},
  "sites": {"allow": ["shop", "news"], "deny": ["*.internal"]}
}
```

* `version`：非空字符串，名单变更必须换版本号。
* `effective_at`：ISO 8601 时间（带 `Z` 或时区偏移），到点才生效。

`reload(path=None)` 读取名单并原子替换快照，返回 `reloaded`、`version`、
`effective_at`、`previous_version`、`kept`、`reason`：

| 情况 | `reloaded` | `reason` | 快照 |
| --- | --- | --- | --- |
| 新版本且已到生效时间 | `true` | `""` | 替换 |
| 未配置名单文件 | `false` | `no_source` | 保留空名单 |
| 文件/JSON/结构不合法、缺 `version`、时间格式错 | `false` | `invalid_list` | 保留旧名单 |
| 版本号相同、内容也相同 | `false` | `unchanged` | 保留 |
| 版本号相同但内容不同 | `false` | `version_conflict` | 保留旧名单 |
| 新版本但 `effective_at` 在未来 | `false` | `not_effective_yet` | 暂存，下次到点生效 |
| 暂存版本到点后再次 `reload()` | `true` | `activated` | 替换 |

整个 `reload` 持锁，校验全部通过才替换快照；失败路径只返回结果、不碰当前名单，
因此并发准入读到的始终是完整的一版名单。

模块级默认名单文件用 `configure(path)` 指定，之后 `reload()` 重新读取该文件。
没有名单文件时为空名单（全部放行，`list_version` 为 `None`）。

## 审计与输出

拒绝会写审计：`at`、`task`、`source`、`site`、`fingerprint`、`reason`、
`reason_text`、`list_version`、`list_effective_at`（重复任务另带 `duplicate_of`）。
审计条目同时随准入结果以 `audit` 字段返回；放行不写审计。

* `status()`：名单版本、生效时间、路径、装载时间、当前名单内容与计数。
* `summary()`：按站点汇总 `admitted`/`rejected`/`reasons`，附 `totals`。
* `audit_log()`：全部拒绝审计。
* `export_rejections(path=None)`：导出拒绝记录（JSON，含导出时的 `list_version`）。
* `reload_history()`：最近的名单热更新结果（含失败原因）。

需要多实例或可控时钟时用 `Guard(path, now=...)`；`reset()` 清空默认准入器。
