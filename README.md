# pushdown

采集结果增量推送：按批次号和水位记录已推送位置，断点续推，下游确认后推进水位。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 接口

- `push(batch)`：推送一批 dict 记录，逐条附 `_version`（全局递增）和 `_batch`（批次号），
  返回 `{"pushed", "confirmed", "batch", "token"}`；中途失败抛 `PushError`（带 `.token` 断点、`.pushed` 已推条数）。
- `resume(token)`：token 指向未推完的批次则从断点续推（不重复推已投递记录）；
  指向已推完的批次则视为下游确认，推进水位并返回 `{"pushed": 0, "confirmed": 水位}`。
- `dump_state()` / `load_state(state)`：状态序列化与恢复（可 JSON 化），重启后复现断点。
- `set_transport(fn)`：注入下游传输，`fn(row)` 抛异常即视为推送失败；`received()` 查看默认收件箱。

## 水位与确认口径

- 批次号从 1 顺序分配；水位 = 连续确认的最大批次号，仅在下游用完成批次的 token 调 `resume` 确认后推进。
- 每条记录 `_version` 全局唯一递增，下游据此区分新数据与重放；断点续推从 offset 继续，不重复投递。
- token 格式 `批次号:offset`，一次性消费：已确认或过期的 token 返回 `error` 字段，不静默跳过。

## 参数与错误

- `MAX_BATCH_ROWS = 10000`：单批次条数上限，超出抛 `ValueError`；空批次同样抛 `ValueError`。
- 非 dict 记录抛 `TypeError`；`load_state` 收到结构不合法的状态抛 `ValueError`。
- 未知/过期 token：`resume` 返回 `{"pushed": 0, "token": None, "error": ...}`。
