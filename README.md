# dqrules

纯标准库的数据质量规则引擎，接口为 `run(dataset, rules)`。

## 规则语法

`rules` 为 `{"rules": [...], "thresholds": {"warning": w, "error": e}, "sample_size": n}`，每条规则含 `name`、`type`，可选 `depends_on: [前置规则名]`（前置未全过则跳过并标 `skipped`）：

- `not_null`：`field` 非 None 且非空串
- `unique`：`field` 值首次出现通过，重复违规
- `range`：`field` 可转数值且满足可选 `min` / `max`
- `enum`：`field` 值在 `allowed` 列表内
- `expression`：`expression` 为 Python 表达式，用 `row` 引用当前行，真值通过、异常算违规

旧形式 `{"required": [...]}` 仍可用，返回 `{"passed", "failed"}` 不变。

## 报告字段

`total_rows`、`checks`（执行检查数）、`violations`、`violation_rate`、`level`（`ok` / `warning` / `error`，按 `violation_rate` 对比 `thresholds`，默认 `{"warning": 0.0, "error": 1.0}`）、`thresholds`、`rules`。
`rules` 每项：`name`、`type`、`status`（`passed` / `failed` / `skipped`）、`passed`、`violated`、`sample_rows`（违规行号样本，最多 `sample_size` 条，默认 5）、跳过时附 `skip_reason`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
