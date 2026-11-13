# ratelimit

现状：所有站点共用固定间隔，请求前按间隔等待。

问题：慢站点被压垮，快站点跑不满，限流后不会恢复。

目标：按站点自适应令牌桶，带退避、回升和统计。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
