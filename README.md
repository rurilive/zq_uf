# compressneg

现状：请求固定 gzip，响应固定按 utf-8 解码。

问题：不支持的站点返回乱码，br 和 zstd 被当二进制丢弃。

目标：按站点协商压缩与编码，失败回退 identity，解压失败单独标记。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

用法：`Negotiator` 按站点保存协商记录。未知站点 `headers()` 发全量探测；
`decode()` 按实际 content-encoding 解压、按 头/BOM/meta/内容 判编码，
并记住协商结果。解压失败返回 `decompress_failed` 并保留原始字节
（`failed_responses()` 取、`clear_failed()` 清），未锁定时回退 identity。
`override()` 人工覆盖并锁定，`unlock()` 解锁重探，`selfcheck()` 新站自检。
