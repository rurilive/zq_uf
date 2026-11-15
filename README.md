# retryflow

现状：失败后按任务计数重试三次，站点是否熔断不影响重试。

问题：站点已熔断仍在排队重试，重试预算按任务独立，恢复后一次性回满。

目标：重试与熔断联动，预算按站点共享，恢复后阶梯放量。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
