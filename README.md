# coldhot

现状：归档后在线查询看不到历史数据。

问题：历史一直在线成本高，归档要改 SQL，恢复靠人工。

目标：冷热分层，查询透明路由并合并，恢复按元数据回迁。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
