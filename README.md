# failclass

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

- `classify`：把失败归一到 `timeout` / `blocked` / `not_found` / `error`，证据带状态码、响应头、请求标识。
- `policy`：超时等一会儿再试；封禁换出口并冷却；不存在直接放弃。
- `FailureGovernor`：按站点共享每日额度；放弃进复核清单（可导出 / 重新入队不计次）；失败构成与处理历史可追溯；冷却到点自动恢复。
