# hostshield

现状：全局固定一秒间隔，RTT和状态码都被忽略。

问题：慢站被压垮，多进程各算各的，合计超速。

目标：按站点自适应速率和并发，状态共享，429后降速恢复。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
