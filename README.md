# crawlchain

现状：只生成追踪号，阶段跨度不入库。

问题：抓取、解析、入库日志串不起来，失败不知道卡在哪。

目标：全链路追踪，跨进程传号，输出慢阶段和错误链。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
