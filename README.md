# siteweight

现状：固定顺序等权轮转，反馈只记录不改变权重。

问题：重点站点更新慢，流量变化后不及时，低权站点容易超时。

目标：按成功率和更新频率动态调权，降权可回升，低权有保底配额。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
