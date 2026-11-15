# jsonschema2

现状：只检查必填字段是否存在，类型和嵌套结构不管。

问题：坏结构整条丢弃，能降级的数据也丢，错误定位不到路径。

目标：按路径校验类型和枚举，可修复字段降级入库，其余隔离。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
