# schemadrift

表结构漂移检测：基线比对 + 影响分级。纯 Python 标准库。

```bash
python3 demo.py                          # 对比两版表结构
python3 -m unittest discover -s tests    # 35 条用例
```

## 接口

```python
from schemadrift import snapshot, diff, save_baseline, check, build_alert

base = snapshot({"name": "orders", "env": "prod", "columns": [...]})
live = snapshot({"name": "orders", "env": "prod", "columns": [...]})
report = diff(base, live)                # 差异 + 级别 + 修复建议
```

- `snapshot(table)`：采集结构，生成列指纹与整表指纹。`table` 支持
  `{"name", "env", "columns": [{"name","type","nullable","default","sensitive"}]}`；
  `columns` 也可以只给列名字符串（此时类型/默认值视为未知，不参与漂移判定）。
- `diff(base, target)`：基线比对。`base`/`target` 可以是 `snapshot()` 结果或原始 table。
  可选过滤：`columns=`（按字段）、`kinds=`（按类别）、`min_level=`（按级别）。
- `filter_report(report, ...)`：对已生成的报告再按字段/类别/级别过滤。
- `save_baseline` / `load_baseline` / `list_baselines` / `check`：按环境保存基线并留档。
- `should_alert` / `build_alert` / `dispatch`：按环境区分门槛生成告警。

## 指纹口径

每列采集 4 项属性，加上敏感标记，逐列做 SHA-256 得到列指纹：

| 属性 | 说明 |
| --- | --- |
| `name` | 列名 |
| `type` | 类型（去空白；未声明为 `None`） |
| `nullable` | 可空性（未声明为未知） |
| `has_default` / `default_hash` | 默认值：先规范化序列化再 SHA-256，明文不入指纹 |

- **默认值一律哈希参与**：非敏感列的明文只用于报告展示，指纹里始终是哈希。
- **敏感列只比哈希**：列名命中 `password/passwd/secret/token/api_key/ssn/credit_card/...`，
  或列定义里写 `sensitive: true`；快照和报告都不保存其默认值明文。
- **整表指纹** = 表名 + 排序后的 `[列名, 列指纹]` 列表的哈希。
- **环境不参与指纹**：同一结构在 prod / staging 指纹相同，便于跨环境核对；
  但基线按环境分别落盘，互不覆盖。
- 指纹计算与列顺序无关；同语义值在任何进程下结果一致。

## 差异分级口径

差异只有四类：`added`（新增）、`removed`（删除）、`type`（类型/可空性）、
`default`（默认值）。可空性属于列约束，因此并入 `type` 类，用 `attribute` 区分。

级别只有三档，按“下游取数是否报错 / 口径是否改变”判定：

| 类别 | high | medium | low |
| --- | --- | --- | --- |
| added | NOT NULL 且无默认值（插入会失败） | 可空性未知 | 可空或带默认值 |
| removed | 有结构信息（查询会失败） | 约束信息未知 | — |
| type | 类型变化；可空性收紧 | 可空性放宽 | — |
| default | 默认值被移除且列仍 NOT NULL | 其他默认值变化 | — |

报告字段：`changes`（每条含 `kind/column/level/attribute/before/after/suggestion`）、
`summary`（四类计数）、`level_counts`、`level`（最高级别）、`has_drift`、
`base_fingerprint`/`target_fingerprint`。兼容旧接口：`added`/`removed` 仍为列名列表。

## 多环境、告警与留档

- 基线落盘：`<root>/<env>/<table>.json`，`root` 默认 `./.schemadrift`。
- `check(table, env=...)` 只比对应环境的基线；该环境无基线时记 `baseline_missing=True`，
  不报漂移，也就不会误用别的环境基线。
- 告警按环境区分门槛：`prod`/`staging` 为 `medium`，`dev` 为 `low`，未配置环境默认 `high`
  （见 `schemadrift.alerting.DEFAULT_POLICY`）。告警文本带 `[env]` 前缀，
  敏感列只出现列名与哈希。
- 留档：`check()` 把报告追加到 `<root>/<env>/audit.jsonl`；也可直接
  `archive_report(report, root=...)`。`audit.jsonl` 按环境分文件。

## 文件

- `schemadrift/fingerprint.py`：采集与指纹
- `schemadrift/drift.py`：比对、分级、过滤、报告
- `schemadrift/store.py`：按环境基线、check、留档
- `schemadrift/alerting.py`：按环境告警
- `tests/test_drift.py`：原有断言（未改动）
- `tests/test_features.py`：四类差异、分级、敏感项、多环境、过滤、告警
