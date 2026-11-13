# tenantcrawl

现状：单租户串行执行，租户只用于打标签，目录、配额和配置都全局共享。

问题：A租户能读到B的结果，一个租户拖慢另一个，新增租户要复制代码。

目标：配置、队列、结果目录和配额按租户隔离，共享执行引擎。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
