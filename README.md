# riskrule

安全方向的利用链验证小工具，纯标准库。
按带前置条件的步骤顺序执行、失败即停，记录每步入参/响应/耗时，结论分三档并附链条来源。

## 实现说明

- `verify(chain)` 按顺序逐步执行：先查前置（链内步骤须已通过，链外条件登记为 assumed），再比对 expect/actual，失败即停，后续步骤标 skipped/blocked。
- 每步记录 `input`、`response`、`elapsed_ms` 和前置检查结果；结论分 `usable`/`partial`/`unusable` 三档，partial 带 `reached_step` 与 `failed_at` 及失败原因。
- `replay(chain_id, step_name)` 按步重放，失败步保留当时响应和耗时；`results(chain_id, all_runs=False)` 取历史；`report()` 汇总三档并列出每条失败链的断裂点和提交人 `source`。
- `source` 必填（链条来源落到人），步骤名须唯一且链内前置只能引用更早的步骤，否则抛 `ValueError`。
