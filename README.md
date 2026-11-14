# depcycle

依赖图环检测：入度剥离（Kahn）定位环上节点，Tarjan 求强连通分量，
枚举分量内所有基本环，check(graph) 返回闭合环路径列表。

断边口径：suggest_breaks(graph) 对每个强连通分量求"断边数最少、
并列时总权重最小"的断边集（分量边数 >16 时贪心近似），每项给出
edge、weight、affected 受影响节点列表和 affected_count。

热更新：CycleChecker.preview(node, deps) 更新前增量预检，
update(node, deps) 应用后只重算经过该节点的环。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

图格式：{"a": ["b"]} 无权（权重 1）、{"a": {"b": 2.5}} 带权。

异常：引用不存在的节点、权重非正数、格式非法均抛 GraphError。
