# batchrerun

现状：登记批次的任务清单，重跑时整批再跑一遍。

问题：成功的重复跑，重跑次数没有完整历史，并发重跑会重复写。

目标：只重跑失败分区，批次加锁，重复请求返回进行中，记录重跑历史。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
