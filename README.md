# schemadiff

现状：只比较字段名增删。

问题：类型和默认值变化漏掉，下游影响不知道，差异不分级。

目标：按破坏性分级，结合血缘输出影响路径和修复建议。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
