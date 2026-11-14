# partitionmove

现状：停机改指向，失败不回滚。

问题：业务中断，切换不原子，旧路径已删。

目标：双写，原子切换，校验和回滚。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
