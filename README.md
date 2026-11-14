# filequeue

现状：队列只追加，取走不删除，确认是空操作。

问题：多个消费者重复处理，崩溃后消息不重投，状态错乱。

目标：可见性超时，超时重投，死信，幂等确认。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
