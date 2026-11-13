# alertmerge

现状：每条告警立即发送，没有窗口、去重和恢复汇总。

问题：上游一失败下游几百条同时响，同一根因重复，恢复通知对不上。

目标：按根因和任务组聚合，窗口内一条，恢复发汇总，支持静默和升级。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
