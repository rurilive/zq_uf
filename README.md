# linksieve

现状：整串URL取哈希，首次到期后按固定间隔重访。

问题：追踪参数一变就当新链接，同页变体撑大队列，热点页重访太慢。

目标：按内容指纹判重，合并别名，按抓取历史调整重访间隔。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
