# datacontract

现状：登记字段列表，比较两版的增删。

问题：类型、可空和枚举不校验，破坏性变更混在噪音里。

目标：样本校验、兼容矩阵、消费方影响和版本回滚。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
