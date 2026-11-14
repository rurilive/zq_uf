# crawlprobe

采集前的站点预检模块，纯 Python 标准库实现。预检不通过的站点直接拦在采集队列外，
避免 DNS 不通、证书过期、robots 禁止、站点封禁等问题在采集过程中才暴露。

## 检查项与结论口径

预检依次执行 5 个检查项，每项给出 `ok` / `warn` / `block` 三级严重程度；
站点结论取其中最高级别（`block` > `warn` > `ok`）。

| 检查项 | 说明 | `block`（禁止采集） | `warn`（可采集，带风险） |
| --- | --- | --- | --- |
| `dns` | DNS 解析 | 解析失败（含超时），且跳过后续检查 | — |
| `tls` | HTTPS 证书链与有效期 | 握手失败、证书过期、自签名、主机名不匹配 | 证书 7 天内到期；HTTP 站点跳过此项 |
| `robots` | robots.txt 规则 | `Disallow` 命中、401/403/407、robots 无法获取或解析 | robots 返回 5xx；404/410 视为无限制（ok） |
| `homepage` | 首页 HTTP 状态 | — | 非 2xx（3xx 重定向、4xx/5xx）或请求失败 |
| `ban` | 封禁特征 | 429、503+Retry-After、403+WAF 头、`X-WAF`/`X-Block`/`cf-mitigated` 等响应头、正文命中封禁关键词 | 首页不可用导致无法识别 |

结论字段：

- `ok`：全部通过，正常入队；
- `warn`：没有硬性禁止项但存在风险（如首页 5xx、证书临期），允许入队，原因写入 `reasons`；
- `block`：不可采集，`intercept()` 直接拒绝并给出原因，同时写入拦截记录。

所有异常路径都落成带 `error` 字段（异常类型 + 信息）的检查项，预检本身不抛网络异常；
非法站点参数（空串、非 http(s) 协议、无主机名）抛 `ValueError`。

## 缓存与冷却

- 预检结果按归一化后的站点（scheme + host + port）缓存，默认 TTL 1800 秒；
- `block` 站点进入冷却（默认 900 秒）：冷却期内直接返回缓存结论、不重试，
  冷却结束后自动重新预检，恢复则放行；
- `cache_info()` 查看缓存快照（剩余有效期、冷却标记），`probe(site, force=True)` 跳过缓存。

## 用法

```python
from crawlprobe import probe, verdict, intercept, cache_info, intercept_records

probe("example.com")          # 完整预检结果：检查项、verdict、reasons、缓存信息
verdict("example.com")        # 只取结论："ok" / "warn" / "block"
intercept("example.com")      # 队列入口：{"allowed": False, "reason": ...} 拦截不可采集站点
cache_info()                  # 缓存快照
intercept_records()           # 拦截记录（仅 block 站点，含原因与冷却时间）
```

需要离线运行或自定义 I/O 时注入网关（实现 `resolve` / `tls_probe` / `http_get` 即可）：

```python
from crawlprobe import ProbeEngine, set_gateway, gateway_scope
engine = ProbeEngine(MyGateway(), ttl=1800, cooldown=900)
with gateway_scope(MyGateway()):   # 或全局切换模块级默认网关
    probe("example.com")
```

## 演示与测试

```bash
python3 demo.py                                  # 离线模拟健康站 / robots 禁止站 / 封禁站
python3 -m unittest discover -s tests            # 29 个用例，全程不发真实请求
```

测试覆盖 DNS 失败、证书过期与握手异常、robots 禁止与缺失、封禁特征（正文/响应头/状态码）、
缓存命中与过期、block 站点冷却重试、队列拦截与非法参数错误。
