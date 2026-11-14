# restoredrill

现状：演练直接返回成功，不恢复也不校验。

问题：缺块要等真恢复才发现，演练可能覆盖生产，RPO 和 RTO 说不清。

目标：隔离目录恢复，校验行数和校验和，输出指标和报告。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
