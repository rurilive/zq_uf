# qualitygate

现状：任一规则失败就阻断整批，豁免参数不生效。

问题：小问题卡发布，豁免永久有效，分级只看规则数。

目标：按失败行数分级，豁免带期，重复检查一致。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
