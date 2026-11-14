# proxycircuit

现状：全局一个熔断开关，任意出口失败就停用整个池子。

问题：一站故障全站停摆，恢复靠人工，没有半开探测。

目标：按出口和站点的三态熔断，单探针半开，阈值和冷却可配。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
