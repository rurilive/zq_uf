# accountrefresh

账号登录态刷新：按账号单飞，提前刷新，指数退避，失败账号隔离并降低调度权重。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 接口与提前量

- `get_token(account)`：令牌距过期不足 `REFRESH_AHEAD`（默认 30s）才刷新，否则复用缓存。
- `refresh(account, force=True)`：跳过缓存与退避强制刷新；同一账号并发只跑一次，其余等待复用结果。
- 账号各自持锁，互不阻塞；`report()` 输出刷新次数、等待复用数、退避中和已隔离账号。
- 每次刷新/复用/退避跳过都写审计，用 `audit_log()` 读取。

## 退避与隔离口径

- 第 n 次连续失败退避 `min(BACKOFF_MAX, BACKOFF_BASE * BACKOFF_FACTOR ** (n-1))` 秒，默认 1/2/4…封顶 60s。
- 连续失败达 `ISOLATE_AFTER`（默认 3）次即隔离，退避再乘 `ISOLATION_FACTOR`（默认 4），隔离期间不再调度。
- 退避到期后自动放行一次试探，成功即清零失败计数并解除隔离。
