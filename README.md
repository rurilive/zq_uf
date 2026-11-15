# jobbudget3

现状：所有任务固定一小时预算，降级不记录。

问题：大任务被误杀，预算耗尽仍全速跑，恢复从头。

目标：按优先级分配，剩余不足降级，进度落盘可续跑。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
