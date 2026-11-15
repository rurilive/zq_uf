# robotscache

现状：不拉取 robots，所有链接放行。

问题：违规抓取，规则更新不失效，子域共用一份。

目标：按主机缓存规则，过期后台刷新，失败保留旧规则。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

用法（`RobotsCache(fetcher=自定义抓取, ttl=秒)`，默认走 urllib）：

- `allowed(url)`：按 (scheme, host, port) 缓存判断；返回 `cached/refreshed/expired/reason`。
- 过期后后台刷新、本次仍用旧规则；刷新失败保留旧规则并记入 `alerts()`。
- 子域先按自己的 robots 判断，没有 robots 时继承主域（`source` 标明 `inherited:`）。
- `enqueue(url)` 固定规则版本，`allowed_queued(ticket)` 按旧规则完成；更新只影响新请求。
- `validate_update(host, test_urls)`：新规则在测试链接上的拒绝范围与旧规则一致才启用，否则保留旧版本并告警。
