# capticket

现状：所有账号共用一条队列，按到达顺序处理。

问题：高优任务排队，超时不回收，回写后重复暂停。

目标：按优先级分派，账号互斥，超时回收，回写确认一次。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
