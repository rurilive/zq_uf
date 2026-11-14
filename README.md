# retrybudget

现状：预算账本随链路传递，每次 attempt 扣 1，耗尽立即抛 BudgetExhausted。

问题：一次请求被放大几十倍，父子链路无法判断在哪层放弃。

目标：可传递预算，耗尽快速失败，兄弟调用不能透支。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

预算分配：with_budget(task, n) 为根任务建账（层级 0）；
with_budget(child, parent) 让子调用共享父级账本（层级 +1），
剩余预算随账本传递，兄弟调用共用同一余额、合计不透支。
单层最多重试 MAX_ATTEMPTS 次，且余额为 0 时 retry 置 False。

放弃口径：余额耗尽时 attempt 立即抛 BudgetExhausted（retryable=False），
异常携带 amplification（全链路已尝试次数，即放大倍数）和 layer（放弃层级）。
未挂预算的任务或非法预算参数抛 BudgetError。
