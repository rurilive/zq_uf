# tablesample

可复现分层采样与误差模块，纯 Python 标准库。

- 分层：按 `stratify_by` 字段分层；数值字段可用 `boundaries` 配边界（如 `[10, 100]` → `<10`、`10-100`、`>=100`）。
- 种子：`plan["seed"]` 显式指定，否则按查询条件（plan 规范化内容）SHA-256 生成，两次抽样可复现。
- 抽样：每层按比例 `ratio` 或固定条数 `size`，`plan["strata"]` 可按层覆盖，二者可混用；样本行带 `_stratum` 分层标记。
- 重放：`sample()` 返回的 `plan` 含解析后的种子，原样传回 `sample(rows, plan)` 即得同一样本。
- 误差口径：`error()` 按字段输出均值、分位数（默认 p50/p90/p99）和误差范围 = 均值 ± z(置信水平)·σ/√n，默认置信水平 95%。
- 长尾层（>= 最大边界，或总体占比 < `tail_share`，默认 5%）在 `long_tail` 中单独统计；层样本量 < `min_size`（默认 30）给出提示。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
