# crawlflow

现状：阶段顺序写死为抓取、解析、入库，上下文放在模块级变量。

问题：新增阶段要改代码，失败整条重来，阶段间靠全局变量传值。

目标：按依赖执行，失败只重跑该阶段和下游，上下文可序列化。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

（实现后补，10 行以内）
