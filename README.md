# quarantine

现状：遇到坏行整批抛错，坏行不保留。

问题：好数据进不去，原始内容丢失，修复后不知道从哪重放。

目标：坏行隔离保留原文，好行入库，按批幂等重放并对账。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
