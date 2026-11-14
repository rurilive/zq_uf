# proxyrotate

现状：每次请求随机取出口，不看会话状态。

问题：会话换出口丢登录态，失败出口仍被选中，回切无记录。

目标：会话绑定，失败轮换，冷却回切和站点隔离。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
