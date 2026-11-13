# taskqueue

现状：先进先出的内存队列，领取后从待处理里移除。

问题：没有优先级、没有租约、卡住的任务不回收。

目标：优先级排序、租约续期、状态机、流程隔离和死信。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
