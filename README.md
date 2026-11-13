# csvrepair

纯Python标准库的CSV修复工具。入口为 `repair(path, schema)`，按schema定位
列数异常和引号问题：能确定的错位按分隔符重切，不能确定的整行保留并标记；
原始行和修复动作写进报告，修复后行数和sha256校验和可核对。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 修复规则

- 引号感知切分后列数等于schema宽度：正常（`ok`）；与朴素切分不同则记`resplit`。
- 列数不足：尾部补空串（`padded`）。
- 引号未闭合：向后拼接后续物理行，拼出宽度吻合的记录记`joined`（字段内换行）。
- 拼接仍不闭合（`unclosed_quote`）或列数超宽（`column_overflow`）：整行原样
  保留进输出文件并标记`kept_raw`，不猜不改。
- 异常路径明确报错：文件不存在抛`FileNotFoundError`，路径是目录抛
  `IsADirectoryError`，schema为空或含空列名抛`ValueError`，不静默跳过。

## 报告字段（path + ".report"，JSON）

- `source`/`output`/`schema`：输入、输出文件与列定义。
- `delimiter`/`quotechar`/`max_join_lines`：本次修复使用的参数。
- `input_lines`/`output_rows`/`fixed_rows`/`kept_raw_rows`：行数核对。
- `sha256`：输出文件的校验和，与返回值的`checksum`一致。
- `actions`：每条非`ok`记录的`line`（起始物理行号）、`action`、`raw`
  （原始行原文）及`kept_raw`的`reason`。

## 参数和阈值

- `DELIMITER = ","`：分隔符，重切和写出统一使用。
- `QUOTECHAR = '"'`：引号字符，按RFC 4180解析。
- `MAX_JOIN_LINES = 20`：字段内换行最多向后拼接的物理行数，超出按
  `unclosed_quote`整行保留。
- 返回值兼容旧契约：`rows`/`count`/`out`保持朴素切分行为；新增`repaired`
  （修复后的记录，`kept_raw`行为原始字符串）、`checksum`、`report_path`。
