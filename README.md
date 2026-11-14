# snapshotgc

现状：回收接口存在但永远不删，租约参数不生效。

问题：快照越积越多，父删子读不出，并发备份和回收互相踩踏。

目标：引用计数加租约，归零才删，回收可中断续跑。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
