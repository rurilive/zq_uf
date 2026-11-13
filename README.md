# replayfail

现状：记录失败 URL，按 URL 重新请求并统计修复和仍失败。

问题：样本缺请求头和请求体，重复回放重复入库，看不清错误变化。

目标：完整样本、错误分类对比、演练库隔离。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
