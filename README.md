# schemavolve

现状：按固定schema读取JSONL，列名必须完全一致，类型原样返回。

问题：上游加列删列改名就整批失败，类型变化不转换，历史文件缺列无法补。

目标：按schema版本兼容读取，补默认值，处理改名和类型转换，给出兼容报告。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

版本化schema为dict：fields声明字段类型，defaults给缺列默认值，renames登记旧列名到新字段的映射。
加列：文件多出的列直接忽略，记入report.dropped_columns；删列：缺列用defaults补齐并记入report.filled_defaults，无默认值则整文件拒绝。
改名：按renames还原，新旧列同时出现视为冲突，抛IncompatibleSchema。

类型能转则转（如字符串9.9转float），记入report.conversions；转不了保留原值，行号和字段记入report.conversion_errors。
不可兼容（缺列无默认值、改名冲突、行非JSON对象）抛IncompatibleSchema，说明行号和原因，整文件不返回。
传list保持旧的严格行为；传dict返回ReadResult（等同list），差异报告在.report属性。
