# partitioncatalog

现状：登记只建目录，查询时扫描目录取名字。

问题：几万个分区要几十秒，增减没有记录，多次写入元数据对不上。

目标：可查询的分区元数据，带范围查询、事件日志和启动修复。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
