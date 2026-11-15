# dagprop

现状：按节点列表顺序执行，依赖不参与。

问题：上游失败下游照跑，重跑全量，依赖热更新不生效。

目标：按入度执行，失败传播，只重跑失败和跳过节点。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
