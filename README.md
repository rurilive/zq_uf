# snapalign

现状：快照与变更各自更新，不记位点。

问题：恢复后数据不一致，快照期间变更丢失，重放无位点。

目标：快照记录起始位点，期间变更缓存，完成后按序应用并校验。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
