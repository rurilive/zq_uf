# etagcache

采集页面的条件请求缓存（纯 Python 标准库）。保存 ETag、Last-Modified 与内容指纹，
下一轮自动带条件头；304 不再覆盖内容；容量按站点和总条数双重限制并 LRU 淘汰。

## 用法

接口保持模块级 `headers(url)` 与 `store(url, response)`：

```python
from etagcache import headers, store

# 请求前取条件头：有缓存且带校验头时返回
# {"If-None-Match": ..., "If-Modified-Since": ...}，否则返回 {}
headers(url)

# 响应写回，response 形如 {"status": 200, "body": "...", "headers": {...}}
result = store(url, response)
```

## 缓存口径

- 每条缓存保存：`body`、`etag`、`last_modified`、`fingerprint`（body 的 SHA-256，
  str/bytes 均可）、`created_at`、`fetched_at`。
- `headers(url)` 命中且有校验头时返回 `If-None-Match` / `If-Modified-Since`，
  响应头名大小写不敏感；无缓存或服务端未给校验头时返回 `{}`。
- `store` 只处理 2xx 与 304：
  - **2xx**：写入/刷新内容与校验头；指纹与旧缓存不同标记 `changed`。
  - **304**：只刷新 `fetched_at` 和 LRU 位置，body、ETag、Last-Modified 原样保留；
    本地无缓存却收到 304 时忽略。
  - 其他状态码（如 5xx）不污染已有缓存。
- 事件字段：`hit`（命中）、`not_modified`（304）、`updated`（2xx 更新）、
  `changed`（内容变化）、`evicted`/`evicted_urls`（淘汰条数与 URL）。
  `cached` 为兼容旧接口保留，`store` 返回中恒为 False。
- 可用 `get(url)` 查看缓存条目，`Cache(...).stats()` 查看累计事件与当前容量，
  `reset(max_per_site, max_entries)` 重建默认缓存。

## 淘汰口径

- 双重上限：每个站点（URL netloc）`max_per_site` 条（默认 100），
  全局 `max_entries` 条（默认 1000）；先做站点淘汰，再做全局淘汰。
- 策略为 LRU：`headers()` 取条件头、304、2xx 写入都会刷新该 URL 的使用时间，
  超限时淘汰 tick 最小（最久未用）的条目，淘汰 URL 随返回值的 `evicted_urls` 输出。
- 需要自定义容量时直接使用 `Cache(max_per_site=..., max_entries=...)` 类。

## 运行

```bash
python3 demo.py                          # 模拟连续两轮采集 + LRU 淘汰
python3 -m unittest discover -s tests    # 原有用例断言不变，另含条件头/304/淘汰/指纹用例
```
