# crawlclock

现状：调度时钟直接用本机时间，同步接口不计算漂移。

问题：多机漂移对不上，夏令时窗口错一小时，漂移没有记录。

目标：按参考时间平滑校正，时区按站点配置，漂移写记录。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
