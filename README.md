# symrecover

单点登录排查小工具。`login(user)` / `report()` 调用方式不变，登录结果可解释、跨应用链路可追踪、异常登录可筛选。

- 每次尝试记录失败原因、所处环节（password/mfa/token/account）和后续动作，MFA 未通过与被绕过单独归类
- 跨应用会话串成一条链路，保留应用跳转顺序与每跳耗时
- 同一账号连续失败的间隔一并记录；失败次数按账号的应用分别统计，并按天汇总
- 异常登录可按 `user`、`app`、`since`/`until` 筛选，历史尝试保留、可按时间回看

```python
from ssoflow import login, report

login({"user": "bob", "events": [
    {"app": "mail", "step": "password", "ok": True, "ts": 1000},
    {"app": "mail", "step": "mfa", "ok": False, "ts": 1005},
]})
report(user="bob", app="mail", since=900, until=2000)
```
