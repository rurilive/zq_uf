# urlqueue

现状：内存队列，先进先出，优先级只记录。

问题：热点和归档混排，插队饿死低优，重启丢顺序。

目标：老化提权，保底，插队上限和落盘恢复。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
