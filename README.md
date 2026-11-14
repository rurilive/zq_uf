# jsonshape

现状：所有站点共用一个顶层字段基线，类型变化不检查。

问题：小站波动淹没大站，类型漂移漏报，冷启动就告警。

目标：按站点和路径的指纹，独立基线，冷启动只学不报。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
