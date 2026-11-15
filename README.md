# urlnorm2

现状：每加入一条链接就重建全部等价类，代表取当前输入。

问题：持续入队时很慢，已有等价关系丢失，代表会漂移。

目标：增量维护等价类，代表稳定，规则变更只重算受影响类。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
