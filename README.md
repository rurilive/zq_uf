# authmatrix

接口认证绕过核查工具：用例按接口 × 身份组合（无凭证 / 过期凭证 / 他人凭证 / 低权限）。

- `matrix(api)` 登记一个接口的组合结果，返回覆盖、未通过、跳过原因和本次回归差异
- 过期凭证按 `expires_at` 实际有效期判断，未真过期记「未测到」，与跳过区分
- 回归只补跑未通过和新增组合，已通过的记跳过；`force=True` 全量重跑
- `report()` 按接口汇总，回归差异分新增 / 修复 / 回归三类

运行 `python demo.py` 查看示例，`python -m unittest discover tests` 跑测试。
