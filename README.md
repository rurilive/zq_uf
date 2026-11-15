# partmove2

现状：停写后直接搬运，切换不是原子操作。

问题：业务中断，读到半份数据，失败没有回滚。

目标：双写加位点，原子切清单，切换前校验，失败回滚。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

实现：
1. `start` 快照旧路径并打开双写，`position` 记位点，`lag` 是未回填行数。
2. `write/delete` 双写期间两侧同写；切换后旧路径在保留期内也同步写，回滚不丢更新。
3. `catch_up(limit=None)` 分批回填，`progress` 看阶段 / 位点 / 两侧行数。
4. `cutover` 要求 `lag == 0`，先 `verify` 两侧行数与内容，再原子替换清单；失败停在旧路径。
5. `rollback` 切回旧路径并保留目标分区 `retention_seconds`，返回回滚清单；旧路径只由 `finalize` 回收。
6. `rehearse` 供低流量分区演练，验证双写与切换过程中查询连续。
