# auditchain

哈希链审计日志：防篡改、可轮转、可区间校验，纯标准库实现。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 链格式

- 每行一条记录：`{"seq","event","prev","hash"}`，`prev` 为前一条的 `hash`，首条为 64 个 `0`。
- `hash = sha256(canonical_json(seq, event, prev))`，canonical 指 key 排序、无空格的 UTF-8 JSON。
- `rotate(path)` 把当前段归档为 `path.N`，`path.manifest.json` 记录每段 `start/end/first/last`。
- 新段首条 `prev` 接上段 `last`，跨文件天然成链；清单首尾哈希用于复核分段完整性。
- `append(event, path, max_bytes=N)` 超限自动轮转。

## 验证口径

- `verify(start, end, path)`：只重算 `[start, end]` 内记录的哈希，边界各多读一条作锚点；缺省全量。
- 返回 `{ok, checked, chain_length, range, break, breaks}`：链长度、验证区间、断链位置（seq/file/reason），不静默跳过。
- 区间内逐条校验：序号连续、`prev` 衔接、哈希重算一致；涉及的分段核对清单首尾哈希。
- `export_report(result, path)` 把验证结果导出为 JSON 对账报告。
