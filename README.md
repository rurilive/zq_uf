# schemagray

现状：结构变更一次应用到全部表。

问题：出问题影响所有下游，灰度不看读写方，回滚靠手工。

目标：按表、任务和读写方向分批，观察错误率，异常自动回滚。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

用法：

```python
from schemagray import plan, apply
p = plan({"tables": [{"name": "dim_region", "access": "read"},
                     {"name": "orders", "access": "write"}],
          "batch_size": 1, "approver": "dba", "summary": "add column"})
r = apply(p, observe=lambda batch, i: {"error_rate": 0.0, "latency_ms": 5.0})
# 只读表先灰度；某批观察超阈值即停批，回滚只影响已放量表，record 保留审批信息
apply({**p, "resume_from": r["resume_from"]})  # 停批后接着上一批继续
```
