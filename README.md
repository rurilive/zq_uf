# sitebudget2

现状：预算只在单个进程内累加和归还。

问题：多进程合计超预算，借还不能跨进程，崩溃后额度不回收。

目标：预算落盘并原子扣减，借还带到期，崩溃按租约回收。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
