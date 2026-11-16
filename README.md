# covmatrix

安全方向的接口检测覆盖小工具：按接口和攻击类型登记检测覆盖面。

- `coverage(api)`：登记一个 `(接口, 环境)`，逐类型记录 hit/miss/skip/未测，多次调用按类型合并，结论互不覆盖。
- `report(api=None)`：按接口返回已覆盖、未覆盖、跳过原因、新增待测；`coverage_drops` 单独提示版本上线后的下降。
- 跳过原因限权限不足、环境不支持、没排期；没写原因的跳过在 `skips_without_reason` 里单列。
- 测试：`python3 -m unittest discover -s tests`；演示：`python3 demo.py`。
