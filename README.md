# retryall

现状：失败固定重试三次，间隔极短。

问题：永久错误也重试，退避无抖动，统计缺失。

目标：错误分类、指数退避、按站点覆盖和统一计数。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
