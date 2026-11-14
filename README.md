# ratemesh

父子速率配额网格：父配额按权重切分给子任务，空闲额度可被兄弟临时借用。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

借还口径：

- `configure(parent, quota, weights, borrow_cap, ttl)` 设定父总额、子权重、借用上限与到期秒数。
- `take(parent, child, n)` 先占自有额度，不足时向空闲兄弟借用，返回父任务当前总占用。
- 借用不超过 `borrow_cap`（默认等于自身额度），兄弟无空闲或超上限即拒绝，父总额绝不超限。
- `give_back(child, n)` 先还借用再还自有；多还、重复还、未知子任务都是幂等空操作。
- 借用到期自动回收（幂等）；`close(parent)` 父退出时回收全部子额度，重复关闭返回 0。
- `stats(parent)` 输出各子额度、借入、借出与回收数。
