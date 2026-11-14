# idemwrite

纯 Python 标准库的幂等写入模块，接口保持 `write(rows, idem_key=None)`。

## 口径

- 版本保护：行可带整数 `version`；已有版本基线的行只能被**严格更高**版本覆盖，相等或更低版本记为冲突；存量行没有版本基线时带版本写入直接接管；不带 `version` 的行沿用旧的直接覆盖行为。
- 幂等重放：同一 `idem_key` 重复提交直接返回**首次结果**（含首次的冲突记录），不再写入；返回里 `idem_hits=1` 表示命中。
- 批量原子：先整批校验再落库，任一行冲突则全批不写，`written=0` 且 `rolled_back` 等于提交行数。
- 冲突记录：每条冲突保留 `incoming`（提交行）、`stored`（存量行及版本）两侧值和 `reason`。
- 返回字段：`written`（写入数）、`idem_hits`（幂等命中）、`conflicts`（冲突）、`rolled_back`（回滚行数）、`rows`（存储快照）。
- 异常路径：入参非法（非字典行、缺 `key`、`version` 非整数、`idem_key` 非字符串）抛 `IdemWriteError`，带明确原因。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
