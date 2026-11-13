# condfetch

现状：普通GET封装，每次整页下载，请求头原样透传但不参与缓存。

问题：服务端支持ETag和Last-Modified却用不上，304被当失败，缓存键只看URL。

目标：条件请求加可共享缓存，支持304、键区分、容量淘汰和后台刷新。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 缓存键

- 键 = 方法（固定 GET）+ URL + 关键请求头（accept、accept-encoding、accept-language、authorization、cookie，小写后参与散列）。
- 其余请求头（如 If-None-Match）不参与键，仅作为验证器由缓存层自动管理。
- 缓存存于 `$CONDFETCH_CACHE_DIR/cache.json`（默认 `~/.cache/condfetch/cache.json`），多进程通过 flock + 原子替换共享同一文件。
- 容量按响应体字节数 LRU 淘汰，默认 64MB，可用 `Cache(path, capacity_bytes=...)` 调整。

## 过期口径

- 响应带 `Cache-Control: max-age=N` 或 `Expires`：新鲜期内直接返回缓存，不发请求。
- 过期后先返回旧值，后台线程带 If-None-Match / If-Modified-Since 条件刷新。
- 无新鲜度信息：每次同步条件回源；304 按成功返回缓存内容，200 更新缓存。
- `no-store` 的响应不写入缓存。
