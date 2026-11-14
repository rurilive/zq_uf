# crawllease

现状：任务租约用内存字典保存，后到者直接覆盖。

问题：两个进程会同时领到同一任务，崩溃后租约不回收。

目标：带过期和栅栏令牌的租约，续期原子，旧持有者写入被拒。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
