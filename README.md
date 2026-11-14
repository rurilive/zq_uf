# datacontract

现状：只比较字段名是否缺失。

问题：类型收紧和默认值变化漏掉，读写方向混在一起，破坏性变更不分级。

目标：双向兼容校验，类型、默认值、枚举和必填逐项判断。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
