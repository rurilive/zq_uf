# jobdep

现状：按列表顺序执行，失败即停。

问题：下游照跑，没有跳过标记，依赖不能热更新。

目标：DAG 执行，失败传播，局部重跑和热更新。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
