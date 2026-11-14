# ratemesh

现状：父子配额共用一个全局计数，不区分借还和上限。

问题：父总额被突破，空闲额度不能借给兄弟，回收泄漏。

目标：按权重分配，借还有上限和到期，回收幂等。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
