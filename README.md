# crawlshard

现状：分片归属只记最后一个节点，任何节点都能写。

问题：切换出现双写，备机没有进度，脑裂无法发现。

目标：租约加单调任期，旧任期拒绝写入，切换从位点续写。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
