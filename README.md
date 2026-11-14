# tablelock

现状：进程内字典锁，无过期和重入。

问题：多进程覆盖，崩溃锁不释放，嵌套死锁。

目标：跨进程租约，过期抢占，重入和幂等释放。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
