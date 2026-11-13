# dqrules

现状：只检查必填字段，违规行单独返回。

问题：没有类型、范围和依赖，规则改动要改代码。

目标：可配置规则、依赖跳过、错误率阈值和结构化报告。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
