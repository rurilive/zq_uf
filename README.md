# etagcache

采集页面的条件请求缓存，只用 Python 标准库。

```python
from etagcache import headers, store

request_headers = headers(url)      # 下一轮请求带上（可能为空）
response = fetch(url, request_headers)
store(url, response)                # 200 写内容，304 只刷新时间
```

运行：

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 缓存口径

每条缓存保存：页面正文 `body`、`ETag`、`Last-Modified`、正文的 sha256
指纹 `fingerprint`、最近抓取时间 `fetched_at`、所属站点 `site`。

| 响应 | 行为 |
| --- | --- |
| `200` | 写入正文、验证器、指纹和抓取时间；正文指纹变化才计一次“更新” |
| `304` | **只刷新 `fetched_at`**，正文、指纹不动；响应带新验证器时一并更新；计一次“命中” |
| 其他状态码 | 不动缓存，避免错误页覆盖已有内容 |

`headers(url)` 按缓存返回 `If-None-Match`（有 ETag 时）和
`If-Modified-Since`（有 Last-Modified 时）；无缓存或无验证器时返回 `{}`，
即全量请求。响应头既可放在 `response["headers"]`（大小写不敏感），
也可放在 `etag` / `last_modified` 字段。`store` 的返回值中 `cached` 恒为
`False`（已拿到真实响应），命中看 `hit`。

## 淘汰口径

- 默认上限：全部站点 500 条，单站点 100 条，可用
  `configure(max_entries=..., max_entries_per_site=...)` 调整。
- 站点按 URL 的 netloc 区分（含端口）。
- 超出时按**最近最少使用（LRU）**淘汰：`store` 写入和 `headers` 读取
  都会把条目移到“最近使用”；先按站点上限淘汰，再按总上限淘汰。
- 刚写入的条目不会被本次淘汰；调小上限会立即淘汰并在返回值里给出条数。
- 统计从 `stats()` 取：`hits`（304 命中）、`not_modified`（304 响应数）、
  `updates`（写入或内容变化）、`evictions`（累计淘汰），以及当前
  `entries` 和 `sites` 分布。
