# proxyburn

现状：出口失败只记集合，选择路径永远可用。

问题：一次失败永久误杀或永不熔断，多进程状态不一致。

目标：连续失败熔断，低频探测，连续成功阶梯恢复。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
