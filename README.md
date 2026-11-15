# failclass

抓取失败按原因分流，重试预算按站点共享。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

- `classify(error, response)`：归一成 `timeout` / `blocked` / `not_found` / `error`，
  证据包含状态码、响应头（自动剔除 cookie/authorization）、URL 和原文。
- `FailureRouter`：超时退避重试；封禁换出口并冷却，冷却期自动结束；
  页面不存在直接放弃进人工复核。站点预算每天重置，复核后重新入队不计费。
- `summary(site)` 看失败构成，`history(site, reason)` 追溯到具体请求，
  `export_review(site=..., fmt="csv")` 按站点导出复核清单，`requeue(task)` 放回队列。
