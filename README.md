# partlife

现状：按天列出过期分区，一次全部删除。

问题：冷热一个策略，下游在读也删，归档和删除不分。

目标：按访问热度分层，活跃租约跳过，归档后删除并分批回滚。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
