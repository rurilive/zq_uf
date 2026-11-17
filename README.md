# privpath

SSRF 回连验证工具：命中以接收端收到回连为准，不再按响应文本猜。

- `start_receiver()` 启动可观测的回连接收端，记录 token、来源地址和请求头。
- `check(url, param)` 注入带唯一 token 的回连 payload；收到回连才算命中，
  没回连但响应异常（网络错误或 5xx）记为可疑并保留原始响应。
- 接收端不可用时抛出 `ReceiverUnavailable` 并停止本轮，不把没回连当成安全。
- `report()` 命中按 (url, param) 去重，可疑项按接口分组，回连记录与命中逐条对应。

运行演示：`python demo.py`；测试：`python -m unittest discover tests`。
