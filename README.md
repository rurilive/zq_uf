# skewwatch

现状：只记录任务总耗时，倾斜被平均掉。

问题：不知道哪个键热，没有处置建议。

目标：按分区和键统计，倍数判定倾斜，给出拆分或加盐建议。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
