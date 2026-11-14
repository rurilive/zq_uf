# jobbudget

现状：所有任务同一超时，进度不保存。

问题：大任务被误杀，预算耗尽仍全速跑，降级写死。

目标：按优先级分配，预算不足降级，断点恢复并审计。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
