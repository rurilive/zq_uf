# sitehealth

现状：只累计事件数，评分固定满分。

问题：慢站和限速站分很高，故障被历史稀释，没有分项。

目标：按成功率、延迟、限速和内容异常加权，快降慢升。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

实现：`report(site, event, ts=None)` 上报事件；`score(site)` 返回分项、总分、趋势、
最近一次变化原因和派单状态。分项按成功率/延迟/限流/内容 0.4/0.2/0.2/0.2 加权，
事件按 30 分钟半衰期衰减；恶化步长 0.6、连续失败 3 次直接对齐，恢复步长 0.15。
总分 <0.5 进观察名单（`watchlist()`），连续 3 天 ≥0.7 移出。
`history(site, day=)` 按天回看，`daily(site)` 按天汇总，`export(site)` /
`export_watchlist()` 导出，`replay(site)` 从原始事件回放评分历史。
