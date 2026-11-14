# linkcheck

纯标准库实现的死链巡检模块：并发去重、完整重定向链、环检测、分类退避、增量导出。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 接口

```python
from linkcheck import check, report, site_report, export_jsonl

summary = check(urls, workers=8)          # 逐 URL 明细进 report()，汇总按站点见 site_report()
rows = report()                           # 每行含 status/chain/hops/category/attempts/elapsed
check(urls, workers=8, export_path="out.jsonl")  # 增量导出，只追加未导出过的行
```

`check(urls, workers)` 与 `report()` 签名保持不变；`workers` 为线程池上限，
实际线程数取 `min(workers, 去重后待查数)`，单 URL 时返回 `workers=1`。
同一 URL 全模块只请求一次，结果按 URL 缓存并跨 `check` 复用，重复引用累加 `references`。

## 并发与限速口径

- `ThreadPoolExecutor` 并发，默认传输为 `urllib`（禁自动重定向，每跳交给引擎记录）。
- 站点 key 为 `host:port`：不同站点之间完全并发；同一站点用站点锁串行化，
  两次请求间隔不小于 `site_interval`（默认 0.2s），即“跨站并发、同站限速”。
- 重定向逐跳跟随（默认上限 10 跳），`chain` 记录每跳 URL/状态码/Location；
  检测到回到链中已访问 URL 标记 `loop`（附 `loop_index`），超上限标记 `redirect_limit`。
- 状态分类：`ok` / `redirect` / `dead`（4xx、5xx 终态）/ `loop` / `redirect_limit` /
  `error`（超时、连接、DNS、SSL、非法 URL）。死链数 = 后四类之和。
- `site_report()` 按站点汇总链接数、死链、重定向、环和累计耗时。

## 退避与重试口径

- 仅对可恢复失败重试：请求超时、连接错误、HTTP 5xx；DNS/SSL/非法 URL/4xx 不重试。
- 退避为指数序列 `backoff * 2**attempt`（默认基数 0.1s），重试次数由 `retries`
  控制（默认 2，即最多 3 次尝试），实际尝试次数写入结果行 `attempts`。
- HEAD 遇 403/405/501 自动回退 GET；重试发生在站点限速锁内，退避也占用站点间隔。
- 结果行含 `elapsed`（单 URL 耗时），汇总含整轮 `elapsed` 与 `requests_issued`。
