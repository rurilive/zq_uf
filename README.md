# changebatch

现状：变更逐条应用，失败保留已写行。

问题：不回滚，重复批次重复写，冲突没有记录。

目标：整批原子推进，失败回滚，重复批次按位点跳过。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
