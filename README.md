# schemavolve

现状：按固定schema读取JSONL，列名必须完全一致，类型原样返回。

问题：上游加列删列改名就整批失败，类型变化不转换，历史文件缺列无法补。

目标：按schema版本兼容读取，补默认值，处理改名和类型转换，给出兼容报告。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
