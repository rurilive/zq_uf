# pipelineview

现状：从配置读 depends 建任务级边，影响面只看直接下游。

问题：实际依赖和配置不一致，字段级看不到，故障时不知道影响哪些报表。

目标：任务和字段两级视图，用运行记录补全依赖，输出影响路径。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）

（实现后补，10 行以内）
