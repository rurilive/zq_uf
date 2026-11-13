# auditlog

现状：按批记录成功和失败条数，回放累加。

问题：定位不到主键，回放重跑整条链路，跨天串不起来。

目标：主键级记录、增量回放、批次检索和结果对比。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
