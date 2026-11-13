# sheetload

现状：按表头读取 CSV，缺列补空字符串。

问题：空行就停、合并单元格错位、类型不转换、重复键不拦。

目标：多工作表、类型转换、约束校验、冲突策略和干跑。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
