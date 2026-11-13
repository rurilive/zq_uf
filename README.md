# retryall

采集脚本统一的错误分类重试库，纯 Python 标准库。入口保持 `call(fn, policy)`，
把超时、连接、限流、服务端错误和永久错误分开处理：永久错误不重试，可重试错误
按类别走带抖动的指数退避与各自上限，重试次数和耗时上报到统一计数器，策略支持
全局配置与按站点覆盖。

## 快速开始

```python
from retryall import call

data = call(fetch_page)                       # 使用默认策略
data = call(fetch_page, {"site": "a.com"})  # 激活 a.com 的站点覆盖
```

对比重构前后的行为：

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 错误分类表

| 类别 | 命中的异常 | 是否重试 | 默认尝试次数 | 退避上限 |
| --- | --- | --- | --- | --- |
| `timeout` | `TimeoutError`、`socket.timeout`、超时型 `http.client`/`urllib`/`ssl` 异常 | 是 | 3 | 2s |
| `connection` | `ConnectionError`（含连接重置）、`RemoteDisconnected`、普通 `ssl.SSLError` | 是 | 3 | 2s |
| `rate_limit` | HTTP 429（`HttpError` 或 `urllib.error.HTTPError`） | 是 | 5 | 30s |
| `server` | HTTP 5xx | 是 | 4 | 10s |
| `permanent` | HTTP 4xx（含 404）、`PermanentError` | 否 | 1 | 无 |
| `transient` | 其他未识别异常的兜底类别（如 `ValueError`） | 是 | 3 | 5s |

`urllib.error.URLError` 会解包其 `reason` 内层异常再分类。HTTP 场景可直接抛
`retryall.HttpError(status, retry_after=...)`，`retry_after` 作为退避等待下限，
仍受该类别上限截断；需要标记不重试的业务错误可抛 `retryall.PermanentError`。

## 默认策略

```python
{
    "base_delay": 0.01,   # 首次退避基准秒数
    "factor": 2.0,        # 指数退避乘数：base * factor ** n
    "jitter": 0.25,       # 抖动比例，实际延迟乘 1 ± jitter 内的随机系数
    "attempts": None,     # 全局尝试次数上限；None 表示由各类别上限决定
    "site": None,         # 激活哪个站点覆盖
    "metrics": None,      # 注入自定义 Metrics；None 用模块级默认计数器
    "classes": {          # 按错误类别的尝试次数与退避上限覆盖
        "timeout":    {"attempts": 3, "max_delay": 2.0},
        "connection": {"attempts": 3, "max_delay": 2.0},
        "rate_limit": {"attempts": 5, "max_delay": 30.0},
        "server":     {"attempts": 4, "max_delay": 10.0},
        "transient":  {"attempts": 3, "max_delay": 5.0},
    },
    "sites": {},          # 站点覆盖，结构与全局策略相同，深度合并
}
```

退避计算：先算指数值，再与异常的 `retry_after` 取大值，按类别 `max_delay`
截断，最后施加抖动并再次截断，保证延迟始终在 `[0, max_delay]` 内。

## 按站点覆盖

```python
policy = {
    "site": "slow-site",
    "sites": {
        "slow-site": {
            "classes": {"server": {"attempts": 2, "max_delay": 0.25}},
        },
    },
}
call(fetch, policy)            # 字典方式激活
call(fetch, Policy(...).for_site("slow-site"))  # 或 Policy 对象方式
```

未命中的站点自动回落到全局策略。

## 统一计数器

每次调用按站点记录 `calls`、`successes`、`failures`，按错误类别记录
`attempts`（含未重试的永久错误）与 `retries`（实际发生的重试），并累计
`backoff`（退避总时长）与 `elapsed`（调用总耗时）。

```python
from retryall import Metrics, call

registry = Metrics()
call(fetch, {"metrics": registry, "site": "a.com"})
print(registry.snapshot())
# 也可直接读取模块级默认计数器：retryall.metrics.snapshot()
```
