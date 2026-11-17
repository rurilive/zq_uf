# exploitchain

安全方向的利用链验证小工具，纯标准库。

把利用链拆成带前置条件的步骤逐步执行：每步记录入参、预期、实际响应和耗时，
某一步失败就停在该步并给出原因。结论分三档：usable（全通）、partial（部分
可用，`reached` 写明打到了哪一步）、unusable（第一步就失败）。

- `verify(chain)`：执行整条链，返回步骤结果、失败点、前置条件和整体结论。
- `replay(chain_id, step)`：用记录的入参单独重放某一步。
- `results(chain_id)` / `report()`：查单链结果或汇总，结论附带链条来源（提交人）。

步骤可带 `run` callable（`run(input)` 现跑），否则用记录的 `actual` 比对 `expect`。
