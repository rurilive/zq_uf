# crawldiagnose

现状：断点只记方法、URL、状态和响应摘要，回放就是读回这条记录。

问题：头和体丢了复现不了，运行时cookie和签名无法重建，没有差异对比。

目标：确定性重放加结构化对比，分请求变化、响应变化和状态变化三类。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 捕获字段

capture(request) 记录：method、url、headers、body、status、
response、response_digest（sha256）、captured_at（时间戳，可用
request 里的 timestamp 指定）。request 里可带 replay 模拟重放时
重新观测到的 request/status/response，缺省字段回落到捕获值。

## 替换规则与对比

重放用固定时钟 REPLAY_TIME 和固定随机源 REPLAY_SEED，结果确定。
替换规则：Cookie/Authorization/Set-Cookie/X-Token 头替换为
<redacted>；timestamp/ts/_t/_ 替换为 REPLAY_TIME；nonce 类用种子
随机数；sign/signature/sig/token 替换为参数名哈希。替换后再对比，
分类为 consistent / request_changed / status_changed /
response_changed，diagnose 返回差异字段（request_diff、
status_diff、response_diff）和可能的改版点 hints。
