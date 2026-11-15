# proxymesh

现状：从出口池随机挑一个，抓取结果只记录。

问题：跨机房延迟差异大，机房故障不切走，坏出口反复被选中。

目标：按健康分和延迟选路，连续失败降权冷却，机房级故障整体切换。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
