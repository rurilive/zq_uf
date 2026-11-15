# rowdedup3

现状：同主键后到覆盖，旧版本能盖新版本。

问题：空值覆盖有效值，冲突静默丢失。

目标：按版本和完整度合并，冲突保留两侧并记录来源。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
