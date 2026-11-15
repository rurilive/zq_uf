# compressneg

按站点协商压缩与字符编码。纯 Python 标准库，无外部依赖。

现状：请求固定 gzip，响应固定按 utf-8 解码。

问题：不支持的站点返回乱码，br 和 zstd 被当二进制丢弃。

目标：按站点协商压缩与编码，失败回退 identity，解压失败单独标记。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

用法：`Negotiator().headers(site)` 未知站点发全量探测、之后只发协商过的方式；
`decode(site, response)` 按实际 `content-encoding` 逐层解压，字符集按
BOM/头/meta/内容判定，返回 `negotiation`/`encoding`/`decompression`/`failure`。
解压失败保留原始字节（`failed_responses()` 取、`clear_failed()` 清）并回退
identity；`override()` 人工覆盖锁定、`unlock()` 解锁重探、`selfcheck()` 新站
自检（失败只影响该站）；传 `store_path` 可把记录原子落盘。
