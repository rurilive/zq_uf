# schemaversion

现状：结构版本直接覆盖，只保留最新。

问题：下游不知道变更，回滚无目标，兼容无记录。

目标：版本链，兼容检查，破坏性标记和回滚。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
