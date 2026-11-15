# pagetrack

现状：每次抓取覆盖旧页面，不记录版本和指纹。

问题：历史版本丢失，增量采集没有续点，订阅方拿不到变更。

目标：保存生成序号和指纹，按站点查询变更，订阅方按序号拉增量。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
