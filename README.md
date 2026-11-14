# tablesync

现状：按主键合并，源端删除不传播，水位为 None。

问题：重复写窗口数据，删除同步不过去，失败无法回退。

目标：增量水位，墓碑传播，分批校验，水位回滚重放。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
