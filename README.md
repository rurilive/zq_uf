# jobbudget2

现状：所有任务固定六十秒预算，不保存进度。

问题：大任务误杀，耗尽仍全速，降级无记录。

目标：按优先级分配，降级，断点恢复和归因。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
