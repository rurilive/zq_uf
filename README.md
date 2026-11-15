# skewwatch

现状：只记录任务总耗时，倾斜被平均掉。

问题：不知道哪个键热，没有处置建议。

目标：按分区和键统计，倍数判定倾斜，给出拆分或加盐建议。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

实现：`observe(task)` 记录每个分区和键的行数与耗时，
按中位数倍数（阈值默认 3，`configure(threshold=…)` 可配）判定倾斜；
返回倾斜判断、按倍数排序的热点键/分区清单，以及加盐或拆分建议（含预估收益秒数）。
重跑只追加历史不覆盖，`report` 看最新，`history` 看全部，`replay` 逐条回放重检；
`configure(period=秒)` 控制检测周期，周期内复用上次结果。
