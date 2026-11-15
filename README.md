# auditsearch

流式审计检索与分页：按时间、操作人、类型过滤，结果按时间排序，内存不随命中数膨胀。

## 检索口径
- `load(events)` 注入数据（可传生成器）；`search(query)` 返回首页 `hits`、`cursor`、`truncated` 和 `page` 信息。
- query 支持等值字段（如 `user`、`type`）和时间范围 `time_from`/`time_to`（含边界，事件时间取 `time` 或 `ts` 字段）。
- 结果按 `(时间, 注入序号)` 升序；`fields=[...]` 按字段裁剪；`max_hits` 超限截断并置 `truncated=True`；`timeout` 秒超时抛 `SearchTimeout`。
- `export(query)` 与 search 同一查询口径，流式导出全部命中。

## 游标口径
- `page(cursor)` 翻页：游标内含查询条件、位置指纹 `(时间, 序号)`、数据版本、过期时间和 SHA1 校验，不重不漏。
- 校验失败抛 `CursorInvalid`，过期抛 `CursorExpired`（`configure(cursor_ttl=秒)` 调整，`None` 不过期）。
- 翻页期间 `load` 变更数据：结果带 `warning` 提示，建议重新检索。
- 等值字段命中索引（默认 `user`、`type`，可 `configure(index_fields=...)`）时走索引，否则自动降级为全量流式扫描。
- `configure(page_size=..., max_hits=..., timeout=...)` 全局调整，也可在调用时传参覆盖；旧的 `{"offset": n}` 游标仍兼容。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
