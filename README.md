# cdcsnapshot

现状：快照和变更流各自更新，没有位点。

问题：恢复不一致，重放重复，快照期间变更丢失。

目标：位点对齐，变更缓存，重放幂等和校验。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
