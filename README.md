# reqcache

纯标准库的请求缓存：键含方法/URL/关键头，按状态码过期，字节容量LRU，多进程共享索引。

## 键规则

- `make_key(method, url, headers)` 生成键：方法大写 + URL + 关键头。
- 关键头只取 accept、accept-encoding、authorization、content-type，名字小写、按名排序。
- 其余头（如 user-agent）不进键；`get`/`put` 直接传字符串时原样作键，也接受 `(method, url, headers)` 元组。

## 容量口径

- 容量按响应 body 的 UTF-8 字节数累计，默认 64 MiB，可用 `configure(capacity_bytes=...)` 调整。
- 超容量时淘汰最久未访问（LRU）的条目；单条超过容量则不缓存。
- 过期按状态码：2xx 300s、301/308 3600s、404 负缓存 30s、5xx 不缓存，`configure(ttl_map=...)` 可覆盖。
- 索引与统计存于 `REQCACHE_DIR`（默认 tmp/reqcache），flock 互斥 + 原子替换，多进程共享。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

`stats()` 返回 hits/misses/size；`report()` 额外给出命中率、淘汰数、过期数和回源数。
