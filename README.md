# cdcordering

现状：按到达顺序覆盖，版本、墓碑和重复投递都不处理。

问题：旧版本覆盖新版本，删除被复活，重复行和冲突无记录。

目标：版本向量收敛，墓碑，幂等，批量原子和冲突记录。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
