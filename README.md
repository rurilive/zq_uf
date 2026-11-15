# partmove2

现状：停写后直接搬运，切换不是原子操作。

问题：业务中断，读到半份数据，失败没有回滚。

目标：双写加位点，原子切清单，切换前校验，失败回滚。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
