# compressneg

采集请求的压缩与字符编码协商模块，纯 Python 标准库。按站点探测支持的
content-coding 与字符集并缓存，失败逐级回退 `identity`；解压失败单独
标记并保留原始字节，不影响下一轮重试。

## 协商顺序

请求侧 content-coding 按以下优先级逐档探测，任何一档失败（解压报错或
站点无视 `Accept-Encoding` 直接回未压缩内容）即记入该站点失败档并换
下一档，`identity` 永远兜底：

```
gzip → deflate → br（可选）→ zstd（可选）→ identity
```

- `gzip`、`deflate`：标准库内置，始终可协商；
- `br`：安装 `brotli` 或 `brotlicffi` 后自动加入；
- `zstd`：Python 3.14 标准库 `zstd`，或安装 `zstandard` 后加入；
- 环境不支持的编码不参与协商，`override()` 指定时直接抛 `ValueError`。

首轮请求只发当前探测档（如 `Accept-Encoding: gzip`）；出现过失败后
附带 `identity` 兜底；锁定后发确认可用的编码（如 `gzip, identity`）。

字符集探测顺序：`Content-Type` 的 charset 参数 → BOM（utf-8/16/32）
→ 页面内 `<meta charset>` / `<?xml encoding>` 声明 → 整体 UTF-8 校验
→ 兜底 `latin-1`。探测成功后随站点缓存，后续请求附带 `Accept-Charset`。

## 接口

```python
headers(site)                 # 该站点本轮请求头，保持原签名
decode(response)              # response={"site","headers","body"}，保持原签名
override(site, encoding, charset)   # 人工覆盖协商结果（缓存可人工覆盖）
profile(site) / profiles()    # 查看单站/全部协商状态
reset(site=None)              # 清单站缓存，不传清全部
```

`decode()` 永不抛异常，返回：

| 字段 | 含义 |
| --- | --- |
| `negotiation` | 站点协商状态：`probing` / `locked` |
| `requested` / `encoding` | 本轮请求档 / 响应实际 content-coding |
| `charset` / `charset_source` | 实际字符集及来源（content-type/bom/meta/sniff/cached） |
| `decompressed` | 解压后的字节；失败为 `None` |
| `raw` | **原始响应字节，保留到排查结束** |
| `failed` / `reason` | 解压失败单独标记，原因写明失败档位与异常类型 |

压缩失败不影响重试：失败档进入站点缓存，下一轮 `headers()` 自动换档，
identity 重试成功后重新锁定。

## 演示与测试

```bash
python3 demo.py                      # 三种站点：gzip 正常 / 回退 identity / 损坏重试
python3 -m unittest discover -s tests
```
