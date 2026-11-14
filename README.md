# crontick

现状：按固定间隔算下一次触发，每个实例到点各自执行。

问题：多实例同时启动，错过不补，时区写死。

目标：错峰、租约互斥、错过补偿和时区换算。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
