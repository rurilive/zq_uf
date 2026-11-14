# sitepolicy

现状：默认策略加站点覆盖，更新直接改全局字典。

问题：冲突看不出以谁为准，热更新读到半新半旧，没有审计。

目标：三层继承、字段级冲突记录、原子切换和审计。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
