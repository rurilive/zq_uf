# dagprop

按依赖调度任务：节点按拓扑序执行，上游失败则下游标记 `skipped` 并写明原因，
不再产出空结果。`run`/`rerun` 复用已成功节点的结果，只重算失败和跳过的节点。
依赖变更新增或修改走 `hot_update`：先校验（缺失依赖、成环），通过后热更新并在
下一次调度生效；校验失败返回明确错误并保留旧图，旧图仍可继续调度。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```
