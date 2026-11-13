# cdcmerge

现状：按到达顺序覆盖主键，删除直接移除。

问题：乱序更新结果不对，重复投递不幂等，事务中间态可见。

目标：按版本收敛、墓碑、事务原子和幂等。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
