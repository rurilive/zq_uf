# contractdiff

现状：只比较字段名，类型和默认值不校验。

问题：类型收紧漏掉，没有方向，破坏性变更不分级。

目标：按读写方向判断，类型收紧和删除列算破坏。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
