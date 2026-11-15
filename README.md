# proxyburn

现状：出口失败只记集合，选择路径永远可用。

问题：一次失败永久误杀或永不熔断，多进程状态不一致。

目标：连续失败熔断，低频探测，连续成功阶梯恢复。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

实现：`proxyburn/burn.py` 状态机 closed → open → half_open → closed。
连续失败到 `CONFIG["failure_threshold"]` 才熔断；熔断期 `use` 不派任务。
探测按 `probe_interval_sec` 低频进行，且只在 `low_traffic_hours` 低流量窗口。
半开按 `recovery_stage` 限制在途并发，连续成功逐级升阶，最后恢复 closed；
恢复中任一失败立即回到 open。状态变化写审计，`history(proxy)` 按出口查询。
状态落盘在 `PROXYBURN_STATE_DIR`（flock 保护），多进程/多机指向同一共享目录即一致。
返回含 state、failures、probe/probe_result、recovery_stage、version。
