# downsample

现状：按固定步长抽点。

问题：峰值被抽没，不同粒度不一致，时间戳对不齐。

目标：窗口聚合、时间对齐、乱序归位、空窗口和多级一致。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
