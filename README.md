# dagrun

现状：按字典顺序串行执行所有任务。

问题：无关任务不并行，上游失败下游照跑，重跑整条链路。

目标：依赖调度、并行、失败传导和断点续跑。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
