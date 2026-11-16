# privpath

SSRF 回连验证：以接收端实际收到的回连为准判定命中，不再按响应文本猜。

- `check(url, param)`：注入带唯一标识的回连 payload，返回 `id`、`callback_at`、`target` 和 `status`（hit / suspicious / clean）；同一 `(url, param)` 默认复用结论，`force=True` 重试。
- `report()`：按接口汇总命中、可疑项和回连记录，命中项与接收端记录按 `id` 逐条对应；可疑项保留原始响应。
- `configure(receiver=...)`：换成可对外暴露的自建接收端；接收端不可用时 `check` 抛 `ReceiverUnavailable` 并停止本轮，`report()` 标记 `round_status=aborted`。
- 回连记录保留来源地址与请求头，`forwarded` / `via` 区分目标直连与代理转发。

运行 `python3 demo.py` 看完整流程；测试 `python3 -m unittest discover -s tests`。仅用于已获授权的安全测试。
