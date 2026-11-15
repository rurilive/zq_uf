# sitequarantine

现状：问题站点只记原因，探测一律返回可用。

问题：样本不保留，恢复靠人工，隔离状态不共享。

目标：隔离带原因、样本和到期，低频探测后连续成功才解除。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
