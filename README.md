# compressneg

现状：请求固定 gzip，响应固定按 utf-8 解码。

问题：不支持的站点返回乱码，br 和 zstd 被当二进制丢弃。

目标：按站点协商压缩与编码，失败回退 identity，解压失败单独标记。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
