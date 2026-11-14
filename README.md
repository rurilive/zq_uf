# jsrender

现状：固定等待两秒，条件只判断一次。

问题：快页面浪费时间，慢页面没渲染完，超时无现场。

目标：组合条件，预算调整，超时快照。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
