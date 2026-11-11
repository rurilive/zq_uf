# audioresample

现状：线性插值加环形缓冲，连续输入正常。

问题：采样率切换爆音，时间戳跳变错位，长跑时钟漂移后缓冲涨满。

目标：重建相位、缺口对齐、漂移补偿，并支持状态序列化恢复。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
