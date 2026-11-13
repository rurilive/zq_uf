# failcluster

现状：只累计失败总数并保存最后一条样本。

问题：分不清网络、解析还是规则，同因不复用，偶发和持续看不出。

目标：按错误类型、站点、选择器和堆栈特征聚类，带影响排序和时间窗。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
