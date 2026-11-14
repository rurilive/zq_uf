# proxybudget

现状：所有站点共用一个全局流量计数。

问题：大站吃光额度，超额中断任务，多进程账目对不上。

目标：按站点和出口计量，超额等待和切换，多进程对账。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
