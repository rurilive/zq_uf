# tablelease

现状：进程内集合记录表名，任何持有者都能获取。

问题：多进程同时写互相覆盖，崩溃后表不释放，重入死锁。

目标：租约带到期和重入计数，过期抢占，释放幂等。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
