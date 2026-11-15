# hostmemo

按站点记忆探测结果：请求头、编码和成功特征（features），带版本号与过期时间，多进程共享一份 JSON 存储。

## 记忆口径

- `remember(host, profile)`：写入画像（headers / encoding / features），版本号 +1，默认 1 小时过期，单条最长 24 小时。
- `get(host)`：记忆未过期且带成功特征才命中（`cached=True`）；否则返回默认画像，并用 `probe` / `retry_after` 提示是否该重探。
- 没有 features 的记忆视为未验证，只存不命中。

## 失效口径

- 过期、探测失败（`report_failure`）、特征变化（`report_features`）都会让记忆失效；失效后第一次 `get` 记录 reprobe 事件并提示重探。
- 探测失败按 2**n 秒退避（上限 300 秒），退避期间 `get` 返回 `probe=False` 和 `retry_after`。
- 存储文件损坏会被挪为 `.corrupt` 并当空库重探，异常记忆不会长期生效。
- `clear(host)` 人工清理单站，`clear()` 清空全部；也可直接删存储文件（默认 `~/.cache/hostmemo/store.json`，可用环境变量 `HOSTMEMO_STORE` 改）。

## 多进程与统计

- 写入用 flock 加锁、临时文件 + `os.replace` 原子替换；`remember(..., expected_version=...)` 用版本号检测并发冲突，冲突时不写入并返回 `conflict=True`。
- `events(host)` 输出命中、失效、重探记录；`stats(host)` 按站点统计命中次数与命中率。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
