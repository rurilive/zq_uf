# crawlprobe2

站点预检批量模块：并发探测、按站点缓存（带过期）、临时/永久失败分流、结果落盘。纯 Python 标准库。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 接口

- `probe(sites, workers=1)`：并发探测。返回 `results`（每站一行：`verdict`/`reason`/`domain`/`cached`/`stale`）和 `ok`、`temporary`、`permanent`、`cache_hits` 计数；`interrupted`/`remaining` 用于中断续跑。
- `verdict(site)`：单站结论，未知站点按 `ok`；永久失败时 `reason` 为判定依据。
- `configure(...)`：`state_dir`（落盘目录）、`ttl`、`temp_ttl`、`perm_ttl`、`timeout`、`prober`（自定义探测函数 `(site, timeout) -> verdict | (verdict, reason) | dict`）。
- `aggregate(results)` 按域名聚合；`changes()` 结论变更记录；`wait_idle()` 等待后台刷新完成。

## 判定口径

- HTTP < 400 → `ok`。
- 5xx、408、429、网络错误、单站超时 → `temporary`（可重试）。
- 其余 4xx → `permanent`（直接拦截，不再实探）。
- 单站点超时只记该站临时失败，不影响整批。

## 缓存口径

- `ok` 缓存 `ttl` 秒；过期后先返回旧结论（计缓存命中，`stale=True`），后台线程刷新。
- `temporary` 缓存 `temp_ttl` 秒，到期后下一次 `probe` 同步重试。
- `permanent` 默认不过期（`perm_ttl` 可改），命中即拦截。
- 落盘（配置 `state_dir` 后）：探测项追加 `probes.jsonl`，当前结论存 `verdicts.json`，结论变更追加 `changes.jsonl`，批量进度写 `progress.json`。
- 中断续跑：批量探测中 Ctrl-C 会保留已完成站点并落盘；进程重进后 `configure(state_dir=...)` 从 `verdicts.json` 恢复，重跑同一批只补探未完成站点。
