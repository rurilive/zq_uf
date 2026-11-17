# vulnticket

安全方向的扫描速率调度工具，每个目标独立维护一档速率。

- 按目标反馈调速：收到限流提示（throttle 原文保留）、响应变慢（latency_ms 超阈值）或错误率上升（error_rate 超阈值）时降速；反馈恢复且冷却结束后逐步提回默认速率。
- 每个目标单独记档，同网段互不影响；进度随 progress 上报，中断后从 resume 位置续扫。
- 连续限流的目标暂停本轮并指数退避，退避结束自动恢复；单目标调整次数有上限，速率有最低档。
- report() 按目标返回当前速率、调整次数、限流提示、续扫位置、暂停原因与预计恢复时间；调整记录保留前后取值、触发原因和限流原文。
- scan(target)/report() 调用方式不变，反馈信号经 target 字典传入（throttle、latency_ms、error_rate、progress、now）。

运行测试：`python3 -m unittest discover -s tests`，演示：`python3 demo.py`。
