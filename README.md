# crawlreport

采集报告对账模块（纯 Python 标准库）。

## 接口

- `build(batch, resume_from=None, now=None)`：生成统计报告。
- `reconcile(report, stored=None)`：报告与入库对账，输出差异。
- `drilldown(report, site=, stage=, reason=, status=, since=, until=)`：下钻明细。
- `export(report, path=None)`：导出 JSON，含版本号和生成时间。

## 统计口径

- `total` 计全部行；无法解析的非字典行只计总数，另计 `unparsed`。
- 状态归四类：`ok` / `failed` / `skipped` / `unknown`，失败与跳过分开，
  明细分别在 `failures`、`skips`。
- `by_site` / `by_stage` 按站点、阶段分桶（总数 + 各状态）；
  `by_reason` 按原因计数，可下钻到原因。
- 报告带 `version` 和 `generated_at`；同一批次同一 `now` 重复生成结果一致。
- 批次中断后，把上次报告传给 `resume_from` 即可续算（按 id 去重，后者覆盖前者）。

## 对账口径

- 应入库行 = 状态 `ok` 且带 `id`（或 `url`/`key`）的行，按 id 与入库行对比。
- 比较行数与校验和（规范化行的 SHA-256）；差异分三类：
  `missing_in_store`（应入库但缺失）、`extra_in_store`（入库多余）、
  `checksum_mismatch`（内容不一致），按 (站点, 阶段, id) 排序，
  并按原因归类计数（`by_reason`）。
- 无 id 的行无法对账，单列在 `unreconcilable`，不参与差异。
- 未提供入库数据（`stored` 参数或批次的 `stored` 键）时按入库为空处理。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
