# ratelimit

按站点自适应令牌桶：`acquire(site)` 取令牌，`report(site, status)` 反馈结果。

- 参数：`interval`（每请求间隔）、`burst`（突发容量）、`sites={"站点": {"interval": …, "burst": …}}` 按站点覆盖，`configure(site, …)` 运行时调整。
- 降速：429/5xx 时间隔乘 `backoff_factor`，上限 `max_interval`。
- 回升：连续 `recover_after` 次 2xx/3xx，间隔除 `recover_factor`，最低回到初始 `interval`，不会更快。
- 并发：多线程共享桶状态，条件变量等待；`stats(site)` 给出请求数、总等待时长、被限次数。

    python3 demo.py
    python3 -m unittest discover -s tests
