# rowdedup3

现状：同主键后到覆盖，旧版本能盖新版本。

问题：空值覆盖有效值，冲突静默丢失。

目标：按版本和完整度合并，冲突保留两侧并记录来源。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

实现：`merge` 按 (表, 主键) 分组；同主键高版本胜出，低版本进 `dropped_details`
（`reason=older_version`）；同版本按字段完整度合并，空值不覆盖有效值；同字段出现
不同非空值记为冲突，合并行取最完整一版，两侧记录、来源和取舍原因都保留在
`conflicts` / `provenance` / `dropped_details` 中；缺失版本按记录时间点之前最近的
有效版本补齐。返回 `deduped`（去重数量）、`conflicts`、`dropped`，按表统计在
`stats`。`replay()` 只读，`export()` 导出原始记录、合并结果和统计。
