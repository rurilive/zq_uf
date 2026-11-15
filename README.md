# metricdedup2

现状：同键直接覆盖，后到的旧版本也能盖掉新版本。

问题：去重状态无限增长，迟到旧版本破坏结果。

目标：同键保留最高版本，按事件时间窗口清理状态，状态可恢复。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
