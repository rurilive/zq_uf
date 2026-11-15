# rowrepair

现状：一条坏行导致整批回退，坏行原文不保留。

问题：好行也丢，修复后不知道从哪回放，回放会重复入库。

目标：坏行隔离并保留原文，好行入库，修复后按批幂等回放。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
