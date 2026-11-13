# retention

现状：按文件修改时间判断过期，逐个删除。

问题：所有数据集同一策略，拷贝后时间失真，删了没痕迹。

目标：按数据集配置、数据时间判断、干跑和审计。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
