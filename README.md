# rowversion

行版本写入：乐观并发控制，纯 Python 标准库。

## 版本口径

- 每行带版本号；新行首次写入传 `version=0`，成功后版本递增。
- `update(key, row, version)`：`version` 与当前版本不一致时返回冲突（`conflict: True`，带 `current` 当前值和 `version` 当前版本），不覆盖；调用方可合并后带新版本重试。
- `update(key, None, version)` 删除，同样校验版本；删除留墓碑，旧版本无法复活已删行，需用墓碑版本重建。
- `get(key)` 返回 `{"row", "version"}`；不存在或已删除返回 `None`。

## 重试口径

- 传 `merge=lambda current, mine: ...` 时冲突自动合并重试，结果带 `retries` 次数。
- `configure(max_retries=, backoff=)` 配置上限和退避基数，退避为 `backoff * 2^n` 秒指数退避。
- 超限返回 `retry_exhausted` 并进入 `manual_queue()` 待人工队列；参数非法抛 `RowVersionError`。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
