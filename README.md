# watermark

现状：用水位最大值判断窗口是否关闭。

问题：乱序数据被直接丢，空闲时窗口不关，延迟写死。

目标：分位水位、允许延迟、空闲推进和侧输出。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
