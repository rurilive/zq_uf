# robotscache

robots 规则的可过期共享缓存，纯 Python 标准库。接口保持 `allowed(url)` 和
`refresh(host)`，通过 `set_fetcher(func)` 注入实际的 robots 拉取逻辑。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 用法与参数

未调用 `configure()` 时保持直连模式（每次 `allowed` 都拉取）；启用缓存：

```python
from robotscache import allowed, configure, set_fetcher

set_fetcher(fetch_robots)
configure(cache_path="/tmp/robots-cache.json", ttl=3600, stale_ttl=86400)
allowed("http://shop/item/1")
refresh("shop")  # 显式刷新，失败抛 RobotsFetchError
```

参数（也可用同名环境变量 `ROBOTSCACHE_PATH`、`ROBOTSCACHE_TTL`、
`ROBOTSCACHE_STALE_TTL`、`ROBOTSCACHE_LOCK_TIMEOUT` 覆盖）：

- `cache_path`：共享缓存 JSON 文件路径；为 `None` 时不启用文件缓存。
- `ttl`（默认 3600 秒）：缓存有效期。未过期直接命中，不发起拉取。
- `stale_ttl`（默认 86400 秒）：过期后可沿用旧规则的宽限期。宽限期内
  过期只触发后台守护线程刷新，当前请求立即用旧规则返回；超过
  `ttl + stale_ttl` 才强制同步刷新。
- `lock_timeout`（默认 5 秒）：跨进程文件锁等待上限，超时抛
  `CacheConfigError`，不静默跳过。

## 缓存文件格式

UTF-8 JSON，按 host 存规则与抓取时间戳（`time.time()` 秒级浮点）；写入走
临时文件 + `os.replace` 原子替换，并经 `fcntl` 排他锁串行化多进程更新：

```json
{
  "version": 1,
  "hosts": {
    "shop": {"rules": {"disallow": ["/private"]}, "fetched_at": 1700000000.0}
  }
}
```

## 命中、过期与告警

所有结果通过 `logging`（logger 名 `robotscache`）输出：`缓存命中`、
`缓存未命中，同步拉取`、`缓存过期，后台刷新`、`后台刷新成功`。后台刷新失败
时输出 WARNING `后台刷新失败，沿用旧规则`，请求仍按旧规则返回；首次拉取、
显式 `refresh` 或超过 `stale_ttl` 的同步刷新失败则抛 `RobotsFetchError`；
缓存文件损坏会记录 ERROR、备份为 `.corrupt-*` 后按空缓存重建。
