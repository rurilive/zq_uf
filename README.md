# dqcontract

现状：契约写死只查首个必填字段，失败只记日志。

问题：阈值改要发版，坏数据照常入库，契约与结构不同步。

目标：可配置非空、范围、枚举和唯一性，按级别隔离或告警。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
