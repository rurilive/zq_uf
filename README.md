# schemagate

现状：结构检查一律放行，变更直接应用到全部表。

问题：下游取数才报错，无法按表回滚，变更没有审批。

目标：破坏性变更阻断，兼容变更按表灰度，失败自动回滚。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
