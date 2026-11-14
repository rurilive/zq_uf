# retentiontag

数据保留标签的级联模块，纯 Python 标准库。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 标签口径

- `tag(table, value)` 挂标签，`keepN` 保留 N 天，N 越小越紧；沿 `link(parent, child)` 血缘向下游传播。
- 生效标签取本表与所有上游中最紧者；下游可收紧不能放宽，被拒的直挂值记在 `propagation()` 的 `rejected`，上游改松后自动生效。
- `propagation()` 记录来源表 `source` 与来源层数 `layer`；标签实际变化写 `audit()`（表、旧值、新值、时间）。

## 级联口径

- `apply(table)` 清理目标表及全部下游，叶子先删、目标表最后，`dependency_order` 给出顺序。
- `batches` 按血缘深度分批，深度越大越先执行；活跃豁免或活跃租约会跳过该表。
- 豁免 `exempt(table, until, approver)` 必须有审批人，到期自动失效，输出带 `active`/`expired` 状态。
- 下游被保留会向上阻塞上游（`blocked by retained dependent`），避免删维表后事实表悬空。
