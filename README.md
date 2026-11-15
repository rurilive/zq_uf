# crawlverify

采集结果核对模块，纯 Python 标准库。接口：`verify(rows, schema)`、`rollback(batch)`。

## 核对口径

`verify` 逐行做字段级核对，任一失败即隔离该行（好行不受影响）：

- **required**：必填字段缺失 → `reason="missing"`
- **fields.type**：类型核对（`int/str/float/number/bool/list/dict`，`bool` 不算 `int`）→ `reason="type"`
- **fields.min/max/choices**：范围核对 → `reason="range"`
- **rules**：跨字段规则 `[{"name", "left", "op", "right"}]`（`right` 为字段名，或用 `value` 给常量）→ `reason="rule"`

规则版本用 `schema["version"]` 切换：`v1` 只查字段存在（旧口径），`v2`（默认）启用全部核对；`register_rule_version` 可注册新版本。

返回 `{"batch", "passed", "isolated", "rolled_back", "sampled", ...}`：`isolated` 带原因和失败明细；`sampled` 按比例（`sample_rate`，默认 0.1，`seed` 可复现）分层抽样——通过行按 `site` 分层、隔离行按错误类型分层，每层至少 1 条，供人工复核。

## 回退口径

`rollback(batch)` 只回退坏行（隔离区中状态为 `isolated` 的行），好行保留入库（`kept`），每次回退写审计（`audit_log()` 可查）。`batch` 传 verify 返回的 dict 或批次号均可；对没有核对记录的批次号保留旧行为（整批 `"all"`）。

隔离区（`quarantine(batch)`）保留原始行和完整核对轨迹；修复后用 `reverify(batch, schema, fixes={行号: {字段: 新值}})` 重新核对，通过的行释放（`released`），轨迹追加保留。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
