# detailcrawl

批量抓取详情页：按域名复用连接、按模板分组解析、按错误类型决定重试。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

```python
from detailcrawl import crawl

result = crawl(urls)                 # 内置 HTTP 抓取，返回解析后的字段 dict
result = crawl(urls, pages.get)      # 自定义抓取函数，原样返回抓取结果
print(result.stats.summary())        # 批次统计；result.stats.details() 看明细
```

## 并发口径

- 连接按 （协议， 域名， 端口） 复用，keep-alive 到批次结束统一关闭。
- 单域名并发上限 max_per_host（默认 4），全局 worker 上限 32。
- 结果顺序与输入 URL 一致；重复 URL 会重复抓取。

## 重试口径

- 仅超时和 5xx 重试，默认重试 2 次（共 3 次尝试），间隔 backoff * 尝试次数秒。
- 404 及其他 4xx 记“跳过”，解析失败记“失败”，均不重试，直接记样本。
- 批次结束输出成功/跳过/失败明细，耗时按抓取、解析、整体分阶段统计。

## 模板分组

- 按页面标签骨架签名分组，同组共用首页面学到的字段规则。
- 字段来源：<title> 元素和带 data-field 属性的元素。
