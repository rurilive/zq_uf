# partquery

现状：遍历数据集目录下所有文件，逐行过滤，全量读进内存。

问题：查一天也扫全表，过滤条件下不去，分区缺失静默返回空。

目标：按时间范围裁剪分区，条件下推到读取，缺失分区单独报错。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
