# ssoflow

单点登录排查工具：让每次登录结果可解释，把跨应用会话串成可审计的链路。

- `login(user)` 调用方式不变，返回失败原因代码、所处环节（authentication/token/mfa/session）、后续动作、跨应用链路与异常项。
- 多因素失败单独归到 `mfa` 环节；要求 MFA 却缺失时记为 `mfa_bypassed` 异常。
- 链路按应用顺序记录，每次跳转保留相对上一应用的耗时；同一账号连续失败记录间隔。
- 失败原因按环节归类，同一账号失败次数按应用分别统计，并按天汇总。
- `report()` 无参返回全局汇总，也支持 `user/app/since/until` 按账号、应用、时间回看异常登录。
- 事件可选 `ts`/`time`（epoch 秒或 ISO-8601）；缺失时用单调合成时间，耗时与间隔仍可计算。

运行演示：`python3 demo.py`；测试：`python3 -m unittest discover -s tests`。
