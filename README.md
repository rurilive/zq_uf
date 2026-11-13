# txrollback

现状：逐行写库，失败直接抛错，已写行不回滚，没有补偿日志。

问题：半批数据留在库里，重跑重复写入，人工回滚靠猜。

目标：批次级回滚，按主键记录补偿，幂等补偿和待人工队列。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
