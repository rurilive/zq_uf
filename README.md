# authmatrix

接口认证绕过核查：按「接口 × 身份」记录组合结果，已测过的不重复跑。

- 四种身份各占一格：`anonymous` / `expired` / `other_user` / `low_privilege`，
  同一接口的四种结论互不覆盖；`skip`（带原因）与 `pass`/`fail` 分开，
  未登记为 `missing`。
- `matrix(api)` 登记组合并返回该接口汇总；`report()` 按接口汇总覆盖、
  未通过、跳过原因、缺失组合、`rerun` 补跑清单与回归差异
  （`new` / `fixed` / `regressed`，以上次 `report()` 为基线）。
- `expired_credential(validity)` 按真实有效期生成过期凭证时间线，
  保证 `expires_at < checked_at`；「过期被拒绝」与「没测到」分别统计。
