# crawlbarrier

现状：一个批次一个到达集合，成功失败都算到达，等待立即返回。

问题：分片失败被掩盖，部分失败不回滚，慢分片拖住整批。

目标：等全部分片，失败触发幂等补偿，超时返回未到达项。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
