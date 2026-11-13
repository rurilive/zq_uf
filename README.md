# schemacheck

现状：只校验必填字段，坏记录单独返回。

问题：类型、长度、枚举都不管，一条坏记录会阻断整批。

目标：多类规则、依赖跳过、错误率预算、流式校验和样本重放。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
