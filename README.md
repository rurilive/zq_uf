# compressneg

现状：请求固定 gzip，响应固定按 utf-8 解码。

问题：不支持的站点返回乱码，br 和 zstd 被当二进制丢弃。

目标：按站点协商压缩与编码，失败回退 identity，解压失败单独标记。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

用法（10 行以内）：

```python
from compressneg import Negotiator
neg = Negotiator(fetch=fetch)          # fetch(site, accept_encoding) -> response
neg.selfcheck("shop")                  # 新站点接入自检，失败只影响该站点
neg.headers("shop")                    # 按协商记录发请求头，未知站点用 identity
r = neg.decode(resp, site="shop")      # 按实际编码解码，r 含 status/encoding/failed
neg.override("shop", charset="gbk")    # 人工覆盖并锁定，自动探测不再改回
neg.unlock("shop")                     # 站点改版后解锁，下次 negotiate 重探
neg.retained["shop"]                   # 解压失败的原始字节，保留到排查结束
neg.clear_retained("shop")             # 排查完成后清理
```
